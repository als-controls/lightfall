# Token Refresh Redesign

## Problem

LUCID's Keycloak access token refresh has been broken despite six fix attempts.
The current design has two independent refresh paths — a timer in `SessionManager`
and an on-demand path in `KeycloakTiledAuth` — that race each other. Both can
call Keycloak's token endpoint with the same refresh token simultaneously. With
Keycloak's rotating refresh tokens, the second call fails (token already consumed),
and may revoke the session entirely.

Additionally, the timer polls every 30 seconds regardless of when the token
actually expires. This is wasteful and imprecise.

A secondary crash occurs when `QThreadFuture` error logging calls `repr()` on
the tiled client argument, which triggers another HTTP request to the Tiled
server — also failing with 401.

### Traceback

```
tiled.client.utils.ClientError: 401: Access token has expired. Refresh token.
  http://bcgtiled.dhcp.lbl.gov:8000/api/v1/metadata/
```

Originates in `tiled_browser_panel.py:_do_fetch` → `client.refresh()`.

## Design

### Principle: Single Refresh Path

One timer, one refresh, no races. The `KeycloakTiledAuth` httpx auth flow
never calls Keycloak — it only reads the current token from `SessionManager`.

### 1. SessionManager: Calculated Timer

Replace the fixed 30-second polling `QTimer` with a calculated one-shot timer.

**On login or successful refresh:**

1. Read `expires_at` from the new session's JWT `exp` claim.
2. Calculate `delay = max(0, (expires_at - now) - 60)` seconds.
   - Refresh fires 60 seconds before expiry.
   - `max(0, ...)` handles edge case where token is already near/past expiry.
3. Start a **single-shot** `QTimer.singleShot(delay_ms, self._do_scheduled_refresh)`.

**On timer fire (`_do_scheduled_refresh`):**

1. If `_refresh_in_progress` is True, skip (guard against re-entry).
2. Set `_refresh_in_progress = True`.
3. Start a `QThreadFuture` that calls `provider.refresh_sync(self._session)`.
4. On success callback (main thread):
   a. Verify: `new_session.user.expires_at > old_session.user.expires_at`.
      If not, treat as failure.
   b. Update `self._session = new_session`.
   c. Log: `"Token refresh OK, next refresh in {delay}s"`.
   d. Schedule the next refresh timer (go to step 1 of "On login or successful refresh").
   e. Reset `_refresh_in_progress = False`.
   f. Reset `_fast_retry_count = 0`.
5. On error callback (main thread):
   a. Increment `_fast_retry_count`.
   b. If `_fast_retry_count <= 3`: schedule retry via `QTimer.singleShot(3000, ...)`.
   c. If `_fast_retry_count > 3`: schedule retry at 30-second intervals
      (fall back to avoid hammering Keycloak). Log a warning.
   d. Reset `_refresh_in_progress = False`.

**State fields to add:**

- `_refresh_in_progress: bool = False` — prevents concurrent refresh attempts.
- `_fast_retry_count: int = 0` — tracks consecutive fast retries.
- `_refresh_timer_id: int | None = None` — tracks the active single-shot timer
  so it can be cancelled on logout.

**Remove:**

- The existing `self._expiry_timer = QTimer(self)` with 30-second interval.
- `_check_session_expiry()` method (replaced by `_do_scheduled_refresh`).
- The `session_expiring` signal emission logic (not used by any consumer).

### 2. SessionManager: Schedule on Login

In `SessionManager.login()`, after `self._session = session` and
`self._set_state(AuthState.AUTHENTICATED)`, call the new
`_schedule_refresh()` method to arm the timer.

### 3. KeycloakTiledAuth: Remove On-Demand Refresh

**`sync_auth_flow` becomes:**

```python
def sync_auth_flow(self, request):
    token = self._get_token()
    if not token:
        yield request
        return

    self._set_auth(request, token)
    response = yield request

    if response.status_code != 401:
        return

    # Token was rejected. Check if SessionManager already refreshed it.
    current_token = self._get_token()
    if current_token and current_token != token:
        self._set_auth(request, current_token)
        yield request
    # Otherwise: give up. The timer will refresh soon.
```

**`async_auth_flow` gets the same treatment** — check for already-refreshed
token, no Keycloak call.

**Delete:** `_refresh_token_sync()` method entirely.

### 4. QThreadFuture: Safe Error Logging

In `threads.py` line 555-559, the error logging formats `self._args` with an
f-string, which calls `repr()` on each arg. For tiled clients, `repr()`
triggers an HTTP request (tiled's `__repr__` queries the catalog), cascading
into a second 401.

**Fix:** Wrap the args/kwargs formatting in a try/except:

```python
try:
    args_repr = repr(self._args)
    kwargs_repr = repr(self._kwargs)
except Exception:
    args_repr = f"<{len(self._args)} args, repr failed>"
    kwargs_repr = "<repr failed>"
```

### 5. Logout Cleanup

In `SessionManager.logout()`:

- Cancel the pending refresh timer (if any).
- Reset `_refresh_in_progress`, `_fast_retry_count`.

## Files Changed

| File | Change |
|------|--------|
| `src/lucid/auth/session.py` | Replace polling timer with calculated one-shot; add `_schedule_refresh`, `_do_scheduled_refresh`; remove `_check_session_expiry`; add state fields; update `login`/`logout` |
| `src/lucid/services/tiled_auth.py` | Remove `_refresh_token_sync`; simplify `sync_auth_flow` and `async_auth_flow` to only check for already-refreshed token |
| `src/lucid/utils/threads.py` | Safe repr in error logging |

## Testing

- Unit test: `_schedule_refresh` calculates correct delay from `expires_at`.
- Unit test: `_do_scheduled_refresh` updates session and reschedules on success.
- Unit test: Fast retry on failure (up to 3), then 30s fallback.
- Unit test: `_refresh_in_progress` guard prevents concurrent refreshes.
- Unit test: `sync_auth_flow` retries with already-refreshed token, does not call Keycloak.
- Unit test: `QThreadFuture` error logging doesn't crash on repr failure.

## What This Does NOT Change

- `KeycloakAuthProvider.refresh_sync` — the actual Keycloak HTTP call is fine.
- The 30-second `_reconnect_timer` for offline mode — separate concern.
- `TiledService` connection logic — unaffected.
- `_SessionAuth` in `logbook/client.py` — separate auth for logbook, not in scope.
