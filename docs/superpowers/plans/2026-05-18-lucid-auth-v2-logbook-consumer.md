# LUCID Auth v2 — Logbook Consumer Migration

> **Per-repo plan executed via superpowers:subagent-driven-development.** This is Step 6 in the [coordination plan](2026-05-17-lucid-auth-v2-coordination.md). The lucid-logbook server's mint endpoint shipped in lucid-logbook MR !5 and is deployed on `bcglucidlogbook.dhcp.lbl.gov` at HEAD `1002b24` (2026-05-18). With the server in place, the LUCID-side consumers cut over to API keys.

**Goal:** Replace every `SessionAuth` (Keycloak Bearer) consumer that talks to the logbook backend with `ServiceKeyAuth("logbook")`. Mint a logbook session key at login alongside the existing tiled key. End state: no LUCID code path reads `session.token` for logbook traffic.

**Spec:** `docs/superpowers/specs/2026-05-17-lucid-auth-v2-design.md`
**Coordination:** `docs/superpowers/plans/2026-05-17-lucid-auth-v2-coordination.md` (§6)
**Branch:** `feature/auth-v2-logbook-consumer` off `feature/notebook-pipelines-impl` @ `96457b1`

---

## Why now

- Step 1-5 done. Tiled side fully migrated. Logbook server mint endpoint deployed.
- After this plan: every consumer of lucid-logbook authenticates with `Authorization: Apikey <secret>`. The Keycloak bearer becomes unused on the wire (refresh machinery still alive — deleted in Step 7).
- Sequenced before Step 7 (auth cleanup) because that plan deletes `SessionAuth` and the refresh timer; both still load-bearing until this plan lands.

## What ships

1. `SessionManager._SERVICE_SCOPES` gains a `"logbook"` entry (empty list — logbook has no granular scope model).
2. `SessionManager._mint_all_service_keys` mints both `tiled` and `logbook` at login.
3. `LogbookClient._do_sync` stops harvesting `session.token`; the sync worker injects `ServiceKeyAuth("logbook")` instead. `_run_sync`'s `auth_token` parameter is removed (unused).
4. `UserSettingsClient.__init__` swaps `SessionAuth()` for `ServiceKeyAuth("logbook")`.
5. A new integration test `tests/auth/test_auth_v2_login_mints_logbook.py` covers the login mint round and the consumer header injection — modelled on `test_auth_v2_login_mints_tiled.py` (the section of `test_session_manager_mint.py` titled "Integration test").

## What does NOT ship

- `SessionAuth` class is **not** deleted (Step 7).
- The bearer-refresh timer is **not** removed (Step 7).
- `session.token` is **not** discarded post-mint (Step 7).
- No change to `lucid-logbook` server (already deployed).

## Files touched

| File | Change |
| ---- | ------ |
| `src/lucid/auth/session.py` | Add `"logbook": []` to `_SERVICE_SCOPES`; extend `urls` dict in `_mint_all_service_keys` with the logbook entry. |
| `src/lucid/logbook/client.py` | Replace `SessionAuth(user_id=user_id)` with `ServiceKeyAuth("logbook")`; drop `auth_token` parameter from `_run_sync` and the harvest block in `_do_sync`. |
| `src/lucid/settings/user_settings_client.py` | Replace `SessionAuth()` with `ServiceKeyAuth("logbook")`. |
| `tests/auth/test_auth_v2_login_mints_logbook.py` | New integration test (see Task 4). |
| `tests/test_user_settings_client.py` | If pytest-httpx fixtures match on Authorization header, verify Apikey shape. (Likely no changes — existing tests don't assert auth header.) |
| `tests/test_logbook_url.py` | No changes expected; URL helper unchanged. |

## Tasks

### Task 1 — Add `logbook` to SessionManager mint round

Modify `src/lucid/auth/session.py`:

1. Update `_SERVICE_SCOPES`:
   ```python
   _SERVICE_SCOPES: dict[str, list[str]] = {
       "tiled": [
           "read:metadata", "read:data",
           "write:metadata", "write:data",
           "register", "create:node",
       ],
       "logbook": [],  # logbook has no granular scope model
   }
   ```
2. Update `_mint_all_service_keys`:
   ```python
   from lucid.logbook.url import get_logbook_base_url
   from lucid.services.tiled_service import get_tiled_base_url

   urls = {
       "tiled": get_tiled_base_url().rstrip("/") + "/api/v1",
       "logbook": get_logbook_base_url().rstrip("/") + "/api/v1",
   }
   ```
   Keep imports lazy (function-local) to match the existing tiled import.

**Tests (extend `tests/auth/test_session_manager_mint.py`):**
- `test_mint_all_service_keys_mints_both_services` — fake `mint_service_key` records every call; assert it's invoked once per service with the right URL and the right scopes.
- `test_mint_logbook_failure_leaves_tiled_intact` — boom on logbook URL only; tiled key still cached.

Commit: `feat(auth): mint logbook session key at login`

### Task 2 — Switch LogbookClient to ServiceKeyAuth("logbook")

Modify `src/lucid/logbook/client.py`:

1. Remove import `from lucid.auth.httpx_auth import SessionAuth`. Add `from lucid.auth.service_key_auth import ServiceKeyAuth`.
2. Change `_run_sync` signature: drop the `auth_token` parameter (3rd positional). Update the inner `client_kwargs["auth"] = SessionAuth(user_id=user_id)` to `client_kwargs["auth"] = ServiceKeyAuth("logbook")`. Drop the `user_id` passthrough to the auth header — logbook now identifies the user via the apikey record. `user_id` is still needed for the logbook-row upsert in the pull phase; keep it as a separate parameter.
3. Change `_do_sync`: delete the `auth_token` harvest block (`session.token` read). Keep the `user_id` harvest. Drop `auth_token` from the `QThreadFuture(_run_sync, ..., auth_token, user_id, ...)` invocation.

**Tests:** Existing `tests/test_logbook_url.py` covers URL resolution. There is no existing LogbookClient HTTP-level test (it's been integration-tested manually). Add one minimal unit test that hits `_run_sync` directly with a mock httpx server and asserts the request carries `Authorization: Apikey <secret>` — `tests/test_logbook_client_auth.py`. Use `pytest-httpx`'s `httpx_mock` fixture; seed `SessionManager._service_keys["logbook"] = MintedKey(...)` and assert the captured request header.

Commit: `feat(logbook): switch sync client to ServiceKeyAuth`

### Task 3 — Switch UserSettingsClient to ServiceKeyAuth("logbook")

Modify `src/lucid/settings/user_settings_client.py`:

1. Replace `from lucid.auth.httpx_auth import SessionAuth` with `from lucid.auth.service_key_auth import ServiceKeyAuth`.
2. Change `self._auth = SessionAuth()` → `self._auth = ServiceKeyAuth("logbook")`.

**Tests:** Existing 30+ tests in `tests/test_user_settings_client.py` should pass unchanged. Add one new test:
- `test_request_carries_apikey_header` — seed `SessionManager._service_keys["logbook"] = MintedKey(secret="abc")`, mock a GET, assert the captured request header is `Authorization: Apikey abc`.

Commit: `feat(settings): switch UserSettingsClient to ServiceKeyAuth`

### Task 4 — Integration test for login → use → roundtrip

New file `tests/auth/test_auth_v2_login_mints_logbook.py`:

End-to-end test of the production login path verifying the logbook key is minted and then used by the consumer. Pattern: monkeypatch `mint_service_key` to return a fake `MintedKey`, stub `get_tiled_base_url` and `get_logbook_base_url`, install a `_StubProvider` like the existing `test_login_runs_mint_round_through_asyncio_to_thread`, run `asyncio.run(sm.login())`, then call `UserSettingsClient.get_instance().get("any")` with httpx_mock and assert the captured request carries `Authorization: Apikey logbook-key`.

Commit: `test(auth): integration test for login mints logbook key`

### Task 5 — Memory + plan updates

After the four code tasks land + pass review:

1. Update `~/.claude/projects/C--Users-rp-workspace/memory/project_lucid_auth_v2.md`:
   - Mark Step 6 done.
   - Update "Next plans" list to leave only Step 7.
   - Update "Open MRs" list with the new MR for this plan.
2. Update `~/.claude/projects/C--Users-rp-workspace/memory/project_notebook_pipelines_status.md`:
   - Coordination status table: Step 6 → DONE.

This task does not produce a code commit; it produces memory file updates outside the repo.

---

## Test plan

Full suite that should be green after Task 4:
```
PYTHONPATH=src .venv/Scripts/python -m pytest \
  tests/auth/ \
  tests/services/ \
  tests/test_user_settings_client.py \
  tests/test_logbook_client_auth.py \
  tests/test_logbook_url.py \
  -v
```

Expected: all previously-passing tests still pass; new tests pass. No regressions in `tests/services/test_tiled_auth_migration.py`.

## Deployment notes

This plan is LUCID-side only — picks up via `pip install -e .` on each workstation. No server deploy needed (lucid-logbook already updated). After this plan merges:

- Users running a fresh LUCID build: get a logbook apikey at login; old bearer never used.
- Users still on a pre-Step-6 build: still send Bearer; lucid-logbook accepts both via its `CombinedAuthMiddleware`. No coordinated cutover required.

## Rollback

If the plan is reverted: LogbookClient + UserSettingsClient revert to SessionAuth; the bearer flow takes over again. The `"logbook"` slot in `_service_keys` would be populated but unread — harmless. No data loss.

---

## Verifications

After all tasks land:
- `grep -rn 'SessionAuth' src/lucid/` should match only `src/lucid/auth/httpx_auth.py` (the class itself, deleted in Step 7).
- `grep -rn 'session\.token' src/lucid/` should match only `src/lucid/auth/session.py` (mint round + refresh machinery, both deleted in Step 7) and `src/lucid/auth/providers/keycloak.py` (Keycloak provider, the only legit reader).
