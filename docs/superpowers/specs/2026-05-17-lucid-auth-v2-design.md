# LUCID Auth v2 — per-service API keys, bearer-refresh retired

## Overview

LUCID's per-service auth is shifting from "Keycloak bearer + refresh treadmill" to "per-service API key minted once at login, held for a week." The refresh machinery (`SessionManager._schedule_refresh`, `_do_scheduled_refresh`, `_on_refresh_success/_failure`, the single-shot timer, `KeycloakTiledAuth._refresh_token_sync` lineage) is **deleted**. The Keycloak **access token** survives only long enough to do the mint round at login; the **refresh token** is discarded outright; the **id_token** is preserved on `SessionManager` (not on `Session`) solely so RP-initiated logout can pass `id_token_hint` to Keycloak.

Each downstream service exposes a Tiled-shape mint endpoint:

```
POST /api/v1/auth/apikey
Authorization: Bearer <keycloak_access_token>
Content-Type: application/json
{"expires_in": 604800, "scopes": [...], "note": "lucid <hostname> <user_sub>"}

→ {"secret": "...", "first_eight": "...", "expiration_time": "...", "scopes": [...], "note": "..."}
```

Subsequent requests use `Authorization: Apikey <secret>`. Keys live one week; LUCID's UI may enforce a much shorter re-login cadence but re-login **never** invalidates an in-flight RunEngine's keys — it re-mints and replaces in cache; old keys age out at TTL.

## Goals

- One credential pattern across every LUCID-backed service: per-(user, service) API key minted at login.
- Retire `SessionManager`'s refresh-token rotation entirely. Bearer is discarded after the initial mint round.
- RunEngine state survives every LUCID UI event short of process exit — re-login, idle timeout, manual lock, key re-mint.
- Pipelines / exporter / tsuchinoko stop forwarding the bearer; the NATS job payload carries a service API key directly.
- als-tiled's already-shipped mint endpoint (Plan A) is the reference shape; lucid-logbook implements the same wire protocol.

## Non-goals

- A transition mode where bearer and API key coexist long-term. The cutover is single-direction: each service migrates atomically (its consumers and its server-side endpoint ship together).
- Per-job key minting. The session key is the job key; pipelines and exporter embed it directly into NATS payloads.
- Centralized auth-broker service. Each downstream service mints its own keys.
- TTL caps on the server side. The client requests one week; servers honor it. If abuse becomes a concern, add a TTL-capping middleware later.
- alshub-api migration. Its only authenticated route uses a static API key already, and the `active-esaf` route is public. If new authenticated routes appear, fold them in.

## Architecture

### Components

1. **`lucid.auth.service_key`** (new module, factored out of `lucid.auth.job_key`) — provides the `mint_service_key(service_url, bearer, *, expires_in, scopes, note)` and `revoke_service_key(...)` primitives. Pure functions over httpx; no LUCID singletons.

2. **`SessionManager.ServiceKeyCache`** (new state field inside `SessionManager`) — a `dict[str, MintedKey]` mapping service name → minted credential record. Read by `SessionManager.get_api_key(service)`. Written by the login mint round and by re-login.

3. **`lucid.auth.service_key_auth.ServiceKeyAuth`** (new httpx.Auth class) — `ServiceKeyAuth("tiled")`, `ServiceKeyAuth("logbook")`, etc. Reads the current key from `SessionManager.get_api_key(self._service)` on every request, sets `Authorization: Apikey <secret>`. Replaces `SessionAuth` and `KeycloakTiledAuth` everywhere **in-process**. For out-of-process consumers (the exporter / pipeline / tsuchinoko subprocesses that receive a key in a NATS payload and have no `SessionManager`), a sibling `StaticApiKeyAuth(secret)` ships in the same module — same wire behavior, captured secret instead of cache lookup.

4. **`lucid.config.schema.LucidConfig.services`** (new config section) — list of `{name, mint_url}` entries. Tiled and logbook entries are populated by default; ops can add more.

5. **Per-service mint endpoint** — Tiled (done), lucid-logbook (new), and any future LUCID-protected service must expose this endpoint.

### Topology

```
Login:
    KeycloakProvider.authenticate() ──► bearer
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                   mint_service_key  mint_service_key  ...
                   (tiled, bearer)   (logbook, bearer)
                          │               │
                          ▼               ▼
                   MintedKey         MintedKey
                          │               │
                          └─► ServiceKeyCache ◄┘
                                    │
                       SessionManager._service_keys
                                    │
                  ───────────────────────────────────
                  ▼          ▼          ▼          ▼
              Tiled UI   Logbook   RE.subscribe  NATS jobs
              (via         (via      writer       (key embedded
              Service-     Service-  (via         in payload)
              KeyAuth      KeyAuth   Service-
              ("tiled"))   ("log-    KeyAuth
                            book"))  ("tiled"))
```

The bearer is dropped from `Session.token` after the mint round; subsequent code paths never read it again. `Session.user.attributes` still holds the decoded JWT claims (already populated by `KeycloakAuthProvider._create_session_from_tokens`), so call sites that wanted *identity information* (not the credential itself) read claims from `user.attributes`.

### Wire protocol (uniform Tiled-shape)

Every LUCID-protected service implements:

| Endpoint                                 | Method | Body                                        | Response                                                                 |
| ---------------------------------------- | ------ | ------------------------------------------- | ------------------------------------------------------------------------ |
| `/api/v1/auth/apikey`                    | POST   | `{expires_in: int, scopes: [str], note: str}` | `{secret, first_eight, expiration_time, scopes, note}`                   |
| `/api/v1/auth/apikey?first_eight=<…>`    | DELETE | (none)                                      | 200/204                                                                  |
| `/api/v1/<any other>` (authenticated)    | *      | service-specific                            | service-specific; accepts `Authorization: Apikey <secret>` or `Bearer …` |

Services validate the apikey by whatever storage model they prefer:

- **DB-backed key table** (Tiled's model). New row per mint; lookup on every request. Best for services that already have a DB.
- **Self-signed JWT** (e.g., lucid-logbook may prefer this). The "secret" is a JWT signed by the service's private key with claims `{sub, exp, iat, scopes}`; validate by signature, no storage. Recommended for services without a key store.

The wire protocol is what's uniform. Storage models are per-service implementation choices. Operationally, prefer self-signed JWT for services that don't already have a key DB — it avoids growing key-management infrastructure where it isn't already paying for itself.

### Scopes

- **Tiled session key:** `[read:metadata, read:data, write:metadata, write:data, register, create:node]`. Excludes `create:apikeys`/`revoke:apikeys` — per-job mint is collapsed, so the session key never mints more keys. Defense in depth.
- **Logbook session key:** `[]` (logbook has no granular scope model — full user authority is implied by holding a valid key).
- **Future services:** documented per service as they're added.

### TTL and re-mint policy

- **Default lifetime:** 7 days (604800s). Matches "no experiment lasts longer than a week."
- **Re-mint on re-login:** every Keycloak login (initial or forced) re-mints all configured services in parallel; cache is replaced. Old keys are **not revoked** — they age out at TTL. The RunEngine's writers, mid-scan, pick up the fresh key on their next request via `ServiceKeyAuth`.
- **Re-mint within a session:** none. There is no idle refresher, no proactive renewal. If a user keeps LUCID open >7 days and a key expires mid-session, surface a re-login prompt via the existing `AuthState` flow.
- **Logout:** clear the cache. Do **not** revoke. Acceptable because the threat model is "physical workstation," not "leaked log file"; a stolen workstation already has worse problems than a 7-day API key, and not revoking guarantees RE state isn't invalidated by a stray logout click.

## Login flow

1. `KeycloakAuthProvider.authenticate()` runs the existing OAuth flow → access token, refresh token, id token.
2. `KeycloakAuthProvider._create_session_from_tokens(tokens)` builds `Session` (populating `user.attributes = decoded_claims` as today).
3. `SessionManager.login()`:
   a. Set `_session = session`, transition to `AUTHENTICATED`. The `state_changed` signal still fires.
   b. (NEW) Kick off `_mint_all_service_keys(bearer)` synchronously on the same thread as login (login is already async/threaded). For each `service` in `LucidConfig.services`:
      - Call `mint_service_key(service.mint_url, bearer, expires_in=604800, scopes=service.scopes, note=f"lucid {hostname} {user_sub}")`.
      - On success → `self._service_keys[service.name] = minted`.
      - On failure → log warning, post a non-blocking toast (`"<service> unavailable, some features disabled"`), leave the cache slot empty.
   c. Once all mints complete (in parallel), **discard credentials from `Session`**: `self._session.token = None`, `self._session.refresh_token = None`, `self._session.id_token = None`. The id_token is copied to `self._id_token_for_logout` on `SessionManager` first so the provider's RP-initiated logout still has it.
   d. Emit `user_changed`.
4. `SessionManager._on_state_for_refresh` is **deleted** (no more refresh scheduling).

The access token's lifetime is now: from the OAuth callback to the end of `_mint_all_service_keys`. After that, it never appears in memory again. The refresh token is discarded at the same moment. The id_token is preserved only on `SessionManager._id_token_for_logout` for the duration of the session, used solely by the Keycloak provider's logout call.

## SessionManager refactor

### Fields removed

```python
self._refresh_in_progress = False
self._fast_retry_count = 0
self._refresh_timer_id: int | None = None
```

### Fields added

```python
self._service_keys: dict[str, MintedKey] = {}
self._keys_lock = threading.RLock()   # cache reads happen from RE threads
```

### Methods removed

- `_schedule_refresh`
- `_on_state_for_refresh` (no listener needed; mint is invoked directly by `login()`)
- `_start_single_shot`
- `_cancel_refresh_timer`
- `timerEvent` (for the refresh timer specifically; if the class uses timer events for anything else, narrow the deletion)
- `_do_scheduled_refresh`
- `_on_refresh_success`
- `_on_refresh_failure`
- The `_get_jwt_exp` static helper (no longer needed; expiry comes from the mint response)

### Methods added

```python
def get_api_key(self, service: str) -> str | None:
    """Return the cached API-key secret for a service, or None if not minted."""
    with self._keys_lock:
        minted = self._service_keys.get(service)
        if minted is None:
            return None
        if minted.is_expired:
            return None
        return minted.secret

def _mint_all_service_keys(self, bearer: str) -> None:
    """Mint a session key for every configured service in parallel.
    Called by login() immediately after authentication; never called again
    during a session. Failures are logged + toasted, not raised."""
    ...

def get_minted_key(self, service: str) -> MintedKey | None:
    """For the export/pipeline dispatcher: return the full record so it can be
    embedded into a NATS job payload."""
    with self._keys_lock:
        return self._service_keys.get(service)
```

### `logout()` changes

```python
async def logout(self) -> None:
    if self._session is None:
        return
    if self._provider:
        try:
            # Note: provider.logout still calls Keycloak's logout endpoint
            # for SSO session termination. The bearer is gone from _session
            # by this point, so KeycloakAuthProvider.logout needs adjustment
            # (it currently passes session.token / session.refresh_token /
            # session.id_token). See "Keycloak provider adjustments" below.
            await self._provider.logout(self._session)
        except Exception as e:
            logger.warning("Logout cleanup failed: {}", e)
    self._session = None
    with self._keys_lock:
        self._service_keys.clear()
    self._set_state(AuthState.UNAUTHENTICATED)
    self.user_changed.emit(ANONYMOUS_USER)
    ...
```

### Keycloak provider adjustments

`KeycloakAuthProvider.logout` currently uses `session.id_token` / `session.refresh_token` to drive RP-initiated logout. Since `SessionManager.login()` will discard those after the mint round, we need to preserve enough state to call Keycloak's logout endpoint.

Options:

1. **Capture id_token at login.** Have `SessionManager.login()` keep the id_token in a private slot (`self._id_token_for_logout`) but still wipe it from `session.token`/`session.refresh_token`. The slot is used only by `_provider.logout()`. Simplest.
2. **Skip RP-initiated logout.** The Keycloak server-side logout endpoint can still be hit without an id_token; it falls back to "logout based on session cookie." LUCID's embedded browser cookie clearing handles the local side. Simpler still, but the user's other Keycloak SSO sessions (other apps in the same realm) won't be terminated.
3. **Pivot logout to use post_logout_redirect_uri only.** Keycloak supports OIDC end-session with just `client_id`; less SSO-tidy but acceptable for our use case.

Recommend Option 1 — keep `id_token` available to the provider via a slot on `SessionManager`, not on the `Session` dataclass. The bearer-and-refresh-token specifically are gone; the id_token is preserved for the logout call but is never used as a bearer.

## Consumer migration

Every site that currently reads `session.token` must change. Categorized below:

### Sites that need an API key (replace bearer with `ServiceKeyAuth(service)`)

| File:line | Service | Action |
| --- | --- | --- |
| `src/lucid/auth/httpx_auth.py:27` | (multiple) | Delete `SessionAuth`. Replaced by `ServiceKeyAuth(service)`. |
| `src/lucid/services/tiled_auth.py:36` | tiled | Delete `KeycloakTiledAuth`. Replaced by `ServiceKeyAuth("tiled")`. |
| `src/lucid/services/tiled_service.py:312-313` | tiled | Replace `"Authorization": f"Bearer {session.token}"` with API-key header pulled from `SessionManager.get_api_key("tiled")`. |
| `src/lucid/logbook/client.py:759-760` | logbook | Stop forwarding `auth_token`; client uses `ServiceKeyAuth("logbook")`. |
| `src/lucid/ui/dialogs/export_dialog.py:484-485` | tiled | Read `SessionManager.get_api_key("tiled")` to embed in NATS export job. |
| `src/lucid/acquire/plans/adaptive.py:128-129` | tiled | Same: pass API key instead of bearer in tsuchinoko NATS payload. |
| `src/lucid/ipc/service.py:321` | tiled | The `"tiled_token"` payload field — rename to `"tiled_api_key"` and read from cache. |
| `src/lucid/exporter/service.py:26, 72, 149` | tiled | Rename `auth_token` field to `tiled_api_key`; `connect_tiled` uses `ApiKeyAuth` instead of `BearerAuth`. |
| `src/lucid/exporter/tiled_utils.py:15-45` | tiled | Replace `BearerAuth` with `StaticApiKeyAuth(secret)` from `lucid.auth.service_key_auth`. Same module used by the executor subprocesses (pipelines, tsuchinoko) — they all consume a literal secret from the NATS payload, not from a cache. |

### Sites that need JWT claims (not a credential) — pivot to `user.attributes`

| File:line | Reason | Action |
| --- | --- | --- |
| `src/lucid/services/access_stamper.py:84-92` | Extracts the user's Keycloak `sub` claim from the JWT for operator-tag stamping. | Read `session.user.attributes["sub"]` (already populated). The doc comment that says "`session.token` is the raw JWT string" should be updated to point to `user.attributes`. |
| `src/lucid/ui/preferences/tiled_settings.py:252-254` | Reads `session.token.claims` for groups (already-broken — `session.token` is a string, not an object with `.claims`). | Read `session.user.attributes["groups"]` directly. |
| `src/lucid/core/application.py:647` | Reads `session.token` — purpose to verify. | Audit at implementation time; either replace with API-key reference or `user.attributes` if it was an identity check. |

### Sites that legitimately need the bearer (mint-window only)

| File:line | Reason | Action |
| --- | --- | --- |
| `src/lucid/auth/providers/keycloak.py:796, 968-974` | `logout()` and `get_user_info()` call Keycloak directly with the bearer. | `logout`: see "Keycloak provider adjustments." `get_user_info`: it's currently unused at runtime; if needed, call it inside `login()` *before* mint+discard. |
| `src/lucid/auth/providers/pam.py`, `local.py` | These are non-Keycloak providers that mint their own session-token strings. | Unaffected by this design — they're not subject to the Keycloak refresh-token problem. The `Session.token` field stays populated for these providers; only the Keycloak path explicitly discards it. |

### Sites that mint on someone else's behalf (now redundant)

| File:line | Current behavior | Action |
| --- | --- | --- |
| `src/lucid/auth/job_key.py` | `mint_job_key`/`revoke_job_key` minted a per-job Tiled key from the bearer. | Move + rename: become `lucid.auth.service_key.mint_service_key`/`revoke_service_key`. The function is unchanged; callers shift from "mint per job" to "mint per service at login." `revoke_service_key` survives but its only caller in v2 is — nowhere, by default; we keep it for adminy/test paths. |

## Per-service migration story

| Service                  | Current state                                                                   | Auth-v2 action                                                                                                                                                                                                                              |
| ------------------------ | ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **als-tiled**            | Mint endpoint shipped (Plan A, MR !1).                                          | None on the server side. LUCID's `ServiceKeyAuth("tiled")` replaces `KeycloakTiledAuth`. Deploy is independent.                                                                                                                              |
| **lucid-logbook**        | No mint endpoint; client uses `SessionAuth` (bearer).                            | Implement `POST/DELETE /api/v1/auth/apikey` returning a self-signed JWT. Validate incoming `Authorization: Apikey …` by JWT signature. LUCID side switches to `ServiceKeyAuth("logbook")` once the endpoint is live. Coordinate cutover.    |
| **alshub-api**           | Static API key for authenticated routes; `active-esaf` is public.                | None for now. Document the gap explicitly: if alshub grows new authenticated routes, fold them into the same mint pattern.                                                                                                                  |
| **lucid.exporter**       | NATS service; receives `auth_token` (bearer) in job payload.                    | Rename payload field `auth_token` → `tiled_api_key`. Executor's `BearerAuth` replaced with `ApiKeyAuth`. LUCID dispatcher reads from session-key cache. Cutover is atomic (LUCID dispatcher and executor ship together — they're in the same repo). |
| **lucid-pipelines**      | Designed for the new model; `mint_job_key` is the seam.                          | Trivial change: dispatcher reads `SessionManager.get_minted_key("tiled")` and embeds it in the job payload instead of calling `mint_job_key`. The executor side is already key-shaped.                                                       |
| **tsuchinoko (LUCID-refactor branch)** | Forwards Tiled token over NATS as bearer.                          | Same as exporter: payload field renamed, executor consumes as API key. Cutover handled in tsuchinoko's own branch.                                                                                                                          |
| **claude/agent integration** (`lucid/claude/agent.py`) | Uses `api_key` for Anthropic, not Tiled/logbook.            | Untouched. This is a third-party API key for Anthropic, unrelated to LUCID's internal auth.                                                                                                                                                  |

## Error handling

| Failure mode                                          | Where caught          | Behavior                                                                                       |
| ----------------------------------------------------- | --------------------- | ----------------------------------------------------------------------------------------------- |
| Service mint endpoint unreachable at login            | `_mint_all_service_keys` | Slot left empty; log warning; non-blocking toast `"<service> unavailable"`. Login succeeds.    |
| Service mint endpoint returns 5xx                     | `_mint_all_service_keys` | Same as unreachable: slot empty, toast, login succeeds.                                        |
| Service mint endpoint returns 401/403                 | `_mint_all_service_keys` | Same — log as auth-failed (user lacks `create:apikeys` on that service). Login still succeeds. |
| `ServiceKeyAuth` finds no key in cache (slot empty)   | per-request           | Yield request without auth header. The downstream call will fail 401 — surface as a "service not authorized; please re-login" toast at the call site. |
| `ServiceKeyAuth` finds expired key                    | per-request           | Same — treat as empty. (At 7-day TTL this should be rare; user has been working > 1 week without re-login.) |
| Server returns 401 on a request despite cached key    | per-request           | One retry with same key (in case of transient server glitch). On second 401, treat as expired: clear that slot, surface re-login toast. |
| RE plan mid-execution; user clicks Logout             | `SessionManager.logout` | No revocation; cache cleared. RE's holding httpx.Auth will return None for the cache lookup → fail on next request. **Document the behavior; do not gate logout on RE state in Phase 1.** (If we want to gate later, it's a UI add.) |
| Keycloak logout endpoint unreachable                  | `provider.logout`     | Warn-log, continue. The local cache is cleared regardless.                                     |
| User keeps LUCID open >7 days                         | `ServiceKeyAuth`       | Cache slot reports expired; user is prompted to re-login on next service call.                |

## Testing strategy

### Unit (LUCID)

- `tests/auth/test_service_key.py` — `mint_service_key` and `revoke_service_key` against a stub httpx transport; verifies request shape, header, error mapping.
- `tests/auth/test_session_manager_mint.py` — verifies:
  - Login mints all configured services in parallel.
  - Bearer is discarded post-mint (`session.token is None` after `login()` returns).
  - `get_api_key(service)` returns the secret; expired keys return None.
  - Re-login replaces cache entries without revoking.
  - Logout clears cache, doesn't revoke.
  - Mint failure for one service does not block login.
- `tests/auth/test_service_key_auth.py` — `ServiceKeyAuth` injects `Authorization: Apikey …`; handles empty slot by yielding without auth.
- `tests/services/test_tiled_auth_removed.py` — confirms `KeycloakTiledAuth` is gone and `tiled.client.from_uri(..., auth=ServiceKeyAuth("tiled"))` works.

### Integration

- `tests/integration/test_auth_v2_e2e.py` — against a bcgtiled test instance + a stub lucid-logbook server with the mint endpoint:
  1. Run the Keycloak OAuth flow (or its mock).
  2. Assert two keys appear in the cache.
  3. Assert `session.token is None`.
  4. Make a Tiled call → succeeds via `Apikey` header.
  5. Trigger a logout → cache empty; Tiled call now fails 401.
- One test exercises "mid-scan re-login": RE active, dispatch a re-login, RE's next write succeeds with the fresh key.

### Per-service contract tests (live in each service's repo)

Each service implementing the mint endpoint ships its own integration tests against the contract:

```python
def test_mint_returns_apikey_shape(authenticated_client):
    r = authenticated_client.post("/api/v1/auth/apikey",
                                  json={"expires_in": 604800, "scopes": [], "note": "test"})
    assert r.status_code == 200
    body = r.json()
    assert "secret" in body and len(body["secret"]) >= 32
    assert "first_eight" in body and "expiration_time" in body

def test_minted_key_authenticates_subsequent_request(authenticated_client):
    ...

def test_revoke_minted_key(authenticated_client):
    ...
```

als-tiled already has these (Plan A). lucid-logbook will grow them as part of its mint-endpoint implementation.

## What this does NOT change

- The non-Keycloak `AuthProvider`s (`local.py`, `pam.py`). They keep `session.token` as their own opaque session-token string. Only the Keycloak provider path discards `session.token` post-mint.
- LUCID's `policy` engine, `Role`/`Permission` machinery. Authz still resolves from `user.roles` populated at login.
- The OAuth callback flow (`OAuthBrowserDialog`, callback server). Unchanged.
- Offline mode and the `_reconnect_timer`. Unchanged.
- alshub-api integration and the ESAF lookup path.

## Open questions

1. **Which thread does the mint round run on?** `login()` is `async def`; the mint helpers are synchronous httpx. Wrap with `asyncio.to_thread` so the event loop isn't blocked. Confirm during implementation that this composes cleanly with the existing login flow's `QThreadFuture` usage.
2. **`get_minted_key()` vs. `get_api_key()`** — current sketch has both (the former for NATS payload embedding, the latter for header injection). Could be a single API returning the record and let consumers pick the field. Implementation-time call.
3. **Where do `LucidConfig.services` defaults live?** Probably in `lucid.config.schema` with two pre-populated entries (`tiled`, `logbook`) read from existing tiled/logbook URL settings. Confirm during plan-write that we don't end up with duplicate URL configuration.
4. ~~What does `ServiceKeyAuth("tiled")` do for a Tiled client constructed in a subprocess (executor)?~~ Resolved: `StaticApiKeyAuth(secret)` ships alongside `ServiceKeyAuth` for the no-SessionManager case (executor subprocesses). Captured in the components list above.
5. **lucid-logbook key storage choice.** Self-signed JWT (recommended in this spec) vs. key-DB. The spec leaves this to the logbook implementation plan. Decision lives in the logbook repo's spec.

## Implementation skeleton

Sequenced so each merges independently and the bearer-refresh code can be deleted as soon as both Tiled and logbook are migrated.

### Stage 0 (precursor — already shipped)

0. als-tiled mint endpoint (Plan A, MR !1). Deployed before Stage 2 in LUCID.

### Stage 1 (LUCID auth primitives)

1. Move `lucid.auth.job_key` → `lucid.auth.service_key`. Rename functions. Update spec references in `lucid-pipelines`. Tests updated.
2. Add `MintedKey` dataclass (in `service_key.py` alongside the mint function).
3. Add `ServiceKeyAuth` class in `lucid.auth.service_key_auth`.
4. Add `LucidConfig.services` config field with Tiled/logbook entries.

### Stage 2 (SessionManager refactor)

5. Add `_service_keys` cache + `get_api_key` + `get_minted_key` to `SessionManager`.
6. Add `_mint_all_service_keys` helper; invoke from `login()` after authentication, before emitting `user_changed`.
7. Discard bearer/refresh_token/id_token from `Session` after mint round; preserve `id_token` on `SessionManager` for logout.
8. Delete refresh machinery: `_schedule_refresh`, `_do_scheduled_refresh`, `_on_refresh_success`, `_on_refresh_failure`, `_get_jwt_exp`, the single-shot timer, `_on_state_for_refresh`, `timerEvent` (narrowly — keep if it's used for other things).
9. Update `KeycloakAuthProvider.logout` to read `id_token` from the new `SessionManager` slot.
10. Tests for the new SessionManager surface.

### Stage 3 (LUCID consumer migration)

11. Switch every site listed in "Consumer migration" → `ServiceKeyAuth("tiled")` / `ServiceKeyAuth("logbook")` / `user.attributes`.
12. Delete `KeycloakTiledAuth` and `SessionAuth`. Grep for stragglers.
13. Tests for each consumer site.

### Stage 4 (exporter + pipelines + tsuchinoko payload migration)

14. lucid.exporter: rename `auth_token` → `tiled_api_key`; switch executor to `Apikey` header; LUCID dispatcher reads from cache.
15. lucid-pipelines: dispatcher embeds session key instead of calling `mint_job_key`. (Executor unchanged — it already speaks Apikey.)
16. tsuchinoko: same payload change on LUCID-refactor branch (separate per-repo plan).

### Stage 5 (lucid-logbook)

17. Implement mint endpoint in lucid-logbook (self-signed JWT recommended; per-repo spec).
18. Deploy. LUCID's logbook client cuts over.

### Stage 6 (cleanup)

19. End-to-end integration test (Tiled + logbook + RE round-trip) on bcgtiled.
20. Memory updates: retire `feedback_lucid_keycloak_claims_location` (still partially valid for non-credential JWT reads, but the credential-flow half is moot).
21. Update README / docs.

Stages 0–4 are LUCID-and-Tiled only; merging them already retires the refresh treadmill for the Tiled half. Stage 5 (lucid-logbook) is gated on the logbook spec landing, but doesn't block the Tiled-side win.

## Related work

- **als-tiled Plan A** (`~/PycharmProjects/als-tiled/docs/superpowers/plans/2026-05-16-user-scoped-api-keys.md`) — Stage 0 of this design; the reference shape every other service implements.
- **2026-04-09 token-refresh redesign** — the refresh-path single-owner shape this design retires. Its `_schedule_refresh` / `_do_scheduled_refresh` machinery is deleted, but the work to centralize refresh ownership was a precondition for being able to delete it cleanly.
- **2026-05-15 notebook-pipelines design § "Auth"** — the per-job-mint model this design generalizes (and collapses).
- **`lucid.auth.job_key`** — the prior-art mint helper. Survives as `lucid.auth.service_key` with no internal logic change.
