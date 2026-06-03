# Lightfall Auth v2 — Cleanup (Step 7, end-state)

> **Per-repo plan executed via superpowers:subagent-driven-development.** This is the final step in the [coordination plan](2026-05-17-lightfall-auth-v2-coordination.md). With Step 6 merged (logbook consumers on `ServiceKeyAuth`), nothing in Lightfall reads `session.token` for service calls — the bearer is dead code waiting for deletion. This plan removes it, deletes the refresh treadmill, and adds the RunEngine logout gate the spec requires.

**Spec:** `docs/superpowers/specs/2026-05-17-lightfall-auth-v2-design.md`
**Coordination:** `docs/superpowers/plans/2026-05-17-lightfall-auth-v2-coordination.md` (§7)
**Branch:** `feature/auth-v2-cleanup` off `master` @ `2adf735`

---

## End state

After this plan merges:

- `SessionManager` has no refresh timer, no `_refresh_in_progress`/`_fast_retry_count`/`_refresh_timer_id`, no `timerEvent`.
- After `_mint_all_service_keys` returns, `session.token`, `session.refresh_token`, `session.id_token` are all cleared. The id_token is preserved separately on `SessionManager._id_token_for_logout` for the Keycloak RP-initiated logout.
- `KeycloakAuthProvider.logout` reads id_token from a session that may have `token=None`. The presence-check switches to "logged in iff session has an id_token to use for logout".
- A logout while `engine.RE.state in {RUNNING, PAUSED}` shows a confirm dialog citing data-write loss; idle RE logs out silently as today.
- `lightfall.auth.httpx_auth.SessionAuth` is deleted (no consumers remain after Step 6).
- `application.py:_handle_ipc_token_request` is deleted (deprecated under auth-v2).
- `access_stamper.py` presence-check switches from `session.token is None` to `session.user is None` to remain compatible with the cleared-bearer state.

The coordination plan's completion criteria become satisfiable:

- `grep -rn 'session\.token' src/lightfall/` matches only `lightfall/auth/session.py` (post-mint clearing assignment) and the Keycloak provider's `_create_session_from_tokens` (the mint window).
- `grep -rn 'SessionAuth\|KeycloakTiledAuth' src/lightfall/` matches only `services/tiled_auth.py` (the `KeycloakTiledAuth` compatibility shim, intentional).

## Out of scope

- Renaming the IPC `tiled_token` field (public contract, see `docs/ipc-architecture.md`).
- `KeycloakTiledAuth` shim removal (kept as a thin subclass of `ServiceKeyAuth("tiled")` for external callers).
- Pre-existing `image_url()` quirk in `UserSettingsClient` (flagged in Step 6 review; deferred to a separate cleanup task — has no live callers).

---

## Files touched

| File | Change |
| ---- | ------ |
| `src/lightfall/auth/session.py` | Delete `_schedule_refresh`, `_do_scheduled_refresh`, `_on_refresh_success`, `_on_refresh_failure`, `_get_jwt_exp`, `_start_single_shot`, `_cancel_refresh_timer`, `_on_state_for_refresh`, `timerEvent`. Drop `_refresh_in_progress`, `_fast_retry_count`, `_refresh_timer_id` state. Disconnect `state_changed` from refresh hook. Add `_id_token_for_logout` slot. After mint, clear `session.token/refresh_token/id_token` and stash id_token. `_attempt_reconnect` keeps connectivity probe but drops the refresh fallback. `reset()` no longer touches the deleted methods. |
| `src/lightfall/auth/providers/keycloak.py` | `logout()` early-return changes from `if not session.token` to `if not session.id_token` (the only token logout uses). |
| `src/lightfall/services/access_stamper.py` | `_operator_identity` presence-check switches `session.token is None` to `session.user is None`. Remove the FORWARD-REFERENCE comment. |
| `src/lightfall/core/application.py` | Delete `_handle_ipc_token_request` method and its `ipc.register_action("auth.token", ...)` registration. |
| `src/lightfall/auth/httpx_auth.py` | Delete file (the `SessionAuth` class). |
| `src/lightfall/ui/mainwindow.py` | `_on_logout` checks engine state; if `RUNNING` or `PAUSED`, show confirm dialog before proceeding. Existing async logout flow preserved. |
| `tests/test_session_refresh.py` | Delete (refresh machinery is gone). |
| `tests/auth/test_session_manager_mint.py` | Add test verifying tokens are cleared after mint and id_token is preserved on the manager slot. |
| `tests/auth/test_auth_v2_full_roundtrip.py` (new) | login -> Tiled read -> logbook read -> logout (idle) -> logout (RE active, confirm). End-to-end with httpx_mock + mocked engine. |
| `tests/auth/test_auth_v2_logout_re_gate.py` (new) | Unit test for the RE-gate dialog: confirm proceeds; cancel aborts. |

## Tasks

### Task 1 — Discard bearer after mint; preserve id_token for logout

Modify `src/lightfall/auth/session.py`:

1. Add to `__init__`:
   ```python
   self._id_token_for_logout: str | None = None
   ```
2. At the end of `_mint_all_service_keys`, after the for-loop:
   ```python
   # Bearer no longer needed: every service is on its own API key. Preserve
   # id_token for RP-initiated logout (KeycloakAuthProvider needs it), then
   # discard. Storing it on the manager (not Session) makes the contract
   # explicit: Session.token is None means "logged in via auth-v2".
   if self._session is not None:
       self._id_token_for_logout = self._session.id_token
       self._session.token = None
       self._session.refresh_token = None
       self._session.id_token = None
   ```
3. Update `SessionManager.logout`: before calling `provider.logout(session)`, restore id_token on the session it passes (so `KeycloakAuthProvider.logout` finds it):
   ```python
   if self._id_token_for_logout and self._session is not None:
       self._session.id_token = self._id_token_for_logout
   ```
   After provider call, clear `self._id_token_for_logout = None`.

**Tests (extend `tests/auth/test_session_manager_mint.py`):**
- `test_tokens_cleared_after_mint` — mint succeeds, then `session.token is None`, `session.refresh_token is None`, `session.id_token is None`, and `sm._id_token_for_logout` matches the original id_token.
- `test_logout_restores_id_token_for_provider` — provider's logout receives a Session whose `id_token` is set, even though it was cleared from the Session post-mint. Use a captures-args MagicMock-based provider.

Commit: `feat(auth): discard bearer after mint, preserve id_token for logout`

### Task 2 — Delete refresh machinery

Modify `src/lightfall/auth/session.py`:

Delete the following methods entirely:
- `_get_jwt_exp` (staticmethod)
- `_schedule_refresh`
- `_on_state_for_refresh`
- `_start_single_shot`
- `_cancel_refresh_timer`
- `timerEvent`
- `_do_scheduled_refresh`
- `_on_refresh_success`
- `_on_refresh_failure`

Delete state fields from `__init__`:
- `self._refresh_in_progress`
- `self._fast_retry_count`
- `self._refresh_timer_id`

Delete the connection: `self.state_changed.connect(self._on_state_for_refresh)`.

In `reset()`, drop the call to `_cancel_refresh_timer()` (the method is gone).

In `_attempt_reconnect`, drop the `self._do_scheduled_refresh()` call in the `_on_done` handler. Keep the connectivity probe + offline-mode toggle.

Delete `tests/test_session_refresh.py` entirely.

After this task, `grep -rn "_schedule_refresh\|_do_scheduled_refresh\|_refresh_in_progress\|_refresh_timer_id" src/lightfall/` returns no matches.

Commit: `refactor(auth): delete bearer-refresh treadmill`

### Task 3 — Switch access_stamper presence-check

Modify `src/lightfall/services/access_stamper.py`:

In `_operator_identity` (around line 91):
- Change `if session is None or session.token is None:` to `if session is None or session.user is None:`.
- Remove the FORWARD-REFERENCE comment block.

Existing tests (`tests/services/test_access_stamper.py` and friends — find them in the suite) must continue to pass; if they seeded `session.token` for the presence-check, update the seed to `session.user` instead.

Commit: `refactor(stamper): presence-check on session.user (auth-v2)`

### Task 4 — Delete IPC `auth.token` handler

Modify `src/lightfall/core/application.py`:

- Delete the method `_handle_ipc_token_request` (around line 634).
- Delete its registration `ipc.register_action("auth.token", ...)` in `_start_ipc` (around line 304).
- Search for tests that hit `auth.token`; delete or update.

After this, `grep -rn "auth.token\|_handle_ipc_token_request" src/lightfall/` returns only the docstring of `_handle_ipc_auth_request` if any (which is `auth.request`, a different handler).

Commit: `refactor(ipc): delete deprecated auth.token handler`

### Task 5 — Delete `SessionAuth` class

Modify `src/lightfall/auth/httpx_auth.py`:

Delete the file entirely. The module had only one symbol (`SessionAuth`); Step 6 removed both consumers. Grep first to be sure nothing imports it:

```
git grep -n "from lightfall.auth.httpx_auth\|import httpx_auth\|SessionAuth" src/lightfall/ tests/
```

If only docstrings/comments mention it: safe to delete. Otherwise: the importer needs migrating (which would be a scope expansion, flag rather than silently extend).

Commit: `refactor(auth): delete SessionAuth class (auth-v2)`

### Task 6 — RE-gate logout dialog

Modify `src/lightfall/ui/mainwindow.py`:

In `_on_logout`:

```python
def _on_logout(self) -> None:
    """Logout current user. Confirm if the RunEngine is active."""
    from lightfall.acquire.engine import EngineState, get_engine

    try:
        engine = get_engine()
        active = engine is not None and engine.state in (
            EngineState.RUNNING, EngineState.PAUSED,
        )
    except Exception:
        active = False

    if active:
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Confirm logout",
            "The RunEngine is currently active. Logging out will not stop "
            "it, but data writes may be rejected once your session ends. "
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

    import asyncio
    from lightfall.utils.threads import QThreadFuture

    def do_logout() -> None:
        asyncio.run(self._session_manager.logout())

    QThreadFuture(do_logout, name="logout").start()
```

Tests for the gate live in the new `tests/auth/test_auth_v2_logout_re_gate.py`. Use the existing MockEngine to drive `engine.state`, monkeypatch `QMessageBox.question` to simulate Yes/No.

Commit: `feat(ui): gate logout on active RunEngine state`

### Task 7 — Integration test: full auth-v2 round-trip

New file `tests/auth/test_auth_v2_full_roundtrip.py`:

Single test that exercises the production path end to end with httpx_mock:

1. Stub Keycloak provider; stub mint helper to return synthetic MintedKey for tiled + logbook.
2. `asyncio.run(sm.login())`.
3. Verify `sm.session.token is None` (Task 1) but `sm._id_token_for_logout` is populated.
4. Verify `get_api_key("tiled")` and `get_api_key("logbook")` both return the synthetic secrets.
5. Have `UserSettingsClient.get(...)` and a stub Tiled call go out with `Authorization: Apikey <secret>`.
6. `asyncio.run(sm.logout())`: provider.logout was called with a session carrying id_token. After logout: `_service_keys` is empty, `_id_token_for_logout is None`, state is UNAUTHENTICATED.

Commit: `test(auth): full auth-v2 round-trip integration test`

### Task 8 — Memory + plan-status updates

After Tasks 1-7 land and the full suite is green:

1. Update `~/.claude/projects/C--Users-rp-workspace/memory/project_lightfall_auth_v2.md`: mark Step 7 done; archive the coordination plan reference.
2. Update `~/.claude/projects/C--Users-rp-workspace/memory/project_notebook_pipelines_status.md`: coordination status table → Step 7 DONE.

This task does not produce a code commit.

---

## Test plan

Final sweep after all 7 code tasks:
```
PYTHONPATH=src .venv/Scripts/python -m pytest \
  tests/auth/ \
  tests/services/ \
  tests/test_user_settings_client.py \
  tests/test_logbook_client_auth.py \
  tests/test_logbook_url.py \
  -v
```

Plus a smoke run on the wider suite to catch indirect breakage:
```
PYTHONPATH=src .venv/Scripts/python -m pytest -q --ignore=tests/integration --ignore=tests/ui 2>&1 | tail -30
```

Expected: no regressions. `tests/test_session_refresh.py` is gone (Task 2); refresh references in any other tests must already be moot.

## Verifications

After all tasks land:
```
# No live reads of the bearer
git grep -n "session\.token" src/lightfall/ | grep -v "session.py\|providers/keycloak.py"
# (empty)

# Refresh machinery gone
git grep -n "_schedule_refresh\|_do_scheduled_refresh\|_refresh_in_progress\|_fast_retry_count\|_refresh_timer_id" src/lightfall/
# (empty)

# SessionAuth class gone
git grep -n "class SessionAuth" src/lightfall/
# (empty)

# IPC auth.token handler gone
git grep -n "_handle_ipc_token_request\|auth\.token" src/lightfall/core/application.py
# (empty)
```

## Rollback

Each task is its own commit, so individual reverts are clean. The order matters for safety:
- Tasks 1+2 (discard bearer + delete refresh) must revert together — keeping the refresh timer alive without the bearer is contradictory.
- Tasks 3, 4, 5 (stamper presence-check, IPC handler delete, SessionAuth delete) are independent.
- Tasks 6+7 (RE-gate UI + integration test) are independent of the others.

A full revert restores the pre-cleanup state with bearer refresh intact. Per-service API keys remain; nothing in this plan re-enables bearer reads downstream.
