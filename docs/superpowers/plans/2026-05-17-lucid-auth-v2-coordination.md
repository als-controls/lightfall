# LUCID Auth v2 — Cross-Repo Coordination Plan

> **For agentic workers:** This plan is a *roadmap*, not an executable task list. It tracks the rollout order across five repos and points at the per-repo plans that do the actual work. Each per-repo plan is self-contained and follows superpowers:subagent-driven-development independently.

**Goal:** Coordinate the rollout of [LUCID Auth v2](../specs/2026-05-17-lucid-auth-v2-design.md) across als-tiled, ncs/ncs, lucid-logbook, lucid-pipelines, and tsuchinoko, ending with the Keycloak-refresh treadmill deleted and every LUCID-protected service authenticated via per-(user, service) API keys.

**Spec:** `docs/superpowers/specs/2026-05-17-lucid-auth-v2-design.md`

---

## Why a coordination plan

Auth v2 spans five repos and the order matters: deleting `SessionManager`'s refresh machinery breaks every still-bearer-based consumer. The atomic-cutover rule from the spec (each service migrates *with* its own consumers) gives us repo-level seams, but the final cleanup (delete refresh, discard bearer post-mint) is gated on *every* service having migrated.

This document is the index — it does **not** specify any code. Per-repo plans live where their code lives, and each is independently executable.

---

## Repo map

| Repo                                 | Spec stage covered            | Plan file                                                                                              | Status         |
| ------------------------------------ | ----------------------------- | ------------------------------------------------------------------------------------------------------ | -------------- |
| als-tiled                            | Stage 0 (mint endpoint)        | `als-tiled/docs/superpowers/plans/2026-05-16-user-scoped-api-keys.md`                                   | **Merged**, MR !1 |
| ncs/ncs (LUCID)                      | Stages 1, 2, 3, 4 (LUCID side) | `ncs/ncs/docs/superpowers/plans/2026-05-17-lucid-auth-v2-core.md`                                       | **To write next** (this session) |
| lucid-logbook                        | Stage 5 (mint endpoint)        | `lucid-logbook/docs/superpowers/plans/YYYY-MM-DD-mint-endpoint.md`                                      | Pending (write when opening repo) |
| ncs/ncs (LUCID logbook consumers)    | Stage 5 LUCID-side             | `ncs/ncs/docs/superpowers/plans/YYYY-MM-DD-lucid-auth-v2-logbook-consumer.md`                           | Pending (write when logbook server is ready) |
| lucid-pipelines                      | Stage 4 (pipelines dispatcher) | `lucid-pipelines/docs/superpowers/plans/YYYY-MM-DD-auth-v2-payload.md`                                  | Pending (small, ~1 task) |
| tsuchinoko (LUCID-refactor branch)   | Stage 4 (tsuchinoko payload)   | `tsuchinoko-phase1/docs/superpowers/plans/YYYY-MM-DD-auth-v2-payload.md`                                | Pending |
| ncs/ncs (LUCID auth cleanup)         | Stage 6 (refresh-deletion)     | `ncs/ncs/docs/superpowers/plans/YYYY-MM-DD-lucid-auth-v2-cleanup.md`                                    | Pending (write last) |

---

## Rollout order

Each step below is a complete plan that must merge + deploy before the next can start.

```
Stage 0 ──► Stage 1+2+3+4-tiled (LUCID core) ──┬──► Stage 5 logbook server ──► Stage 5 LUCID consumer ──► Stage 6 cleanup
   │                                            │
   │                                            ├──► Stage 4 lucid-pipelines (independent)
   │                                            │
   │                                            └──► Stage 4 tsuchinoko (independent)
   │
   └─ already done
```

### 1. als-tiled mint endpoint — **DONE**

Plan A merged. Deployed to bcgtiled. This is the reference shape every other mint endpoint copies.

### 2. ncs/ncs LUCID core plan — **NEXT**

Builds the auth-v2 plumbing in LUCID:

- `lucid.auth.service_key` module (the renamed mint helpers + `MintedKey`)
- `lucid.auth.service_key_auth.ServiceKeyAuth` + `StaticApiKeyAuth`
- `SessionManager._service_keys` cache + `get_api_key()` + `_mint_all_service_keys()` at login
- Migration of all Tiled consumers (`KeycloakTiledAuth` deleted, replaced by `ServiceKeyAuth("tiled")`)
- `lucid.exporter` payload migration (`auth_token` → `tiled_api_key`)
- JWT-claim consumers (`access_stamper`, `tiled_settings`) pivot to `user.attributes`

**Logbook keeps using `SessionAuth` and the bearer until its mint endpoint ships.** The refresh machinery in `SessionManager` is **NOT deleted** in this plan — it survives as the logbook bearer-keepalive. The bearer is also **NOT discarded post-mint** for the same reason.

End state of this plan: Tiled side fully on API keys; logbook unchanged.

### 3. lucid-pipelines dispatcher migration — independent of step 2

In `lucid-pipelines` repo (`~/PycharmProjects/lucid-pipelines`): the LUCID-side dispatcher (which lives in `ncs/ncs/src/lucid/pipelines/`) shifts from calling `mint_job_key` per submit to reading from `SessionManager.get_minted_key("tiled")`. The executor consumes the key as today. ~1 task; trivial.

Can be done in parallel with step 2 since it's wholly LUCID-side and uses the cache the core plan introduces. Sequenced **after** step 2 in practice.

### 4. tsuchinoko payload migration — independent of step 2

In `~/PycharmProjects/tsuchinoko-phase1` on the `LUCID-refactor` branch: the NATS job payload's bearer field is renamed `tiled_api_key`, and the executor uses `StaticApiKeyAuth(secret)`. LUCID-side dispatcher reads from cache.

Sequenced **after** step 2 (depends on cache existing).

### 5. lucid-logbook mint endpoint — separate plan in lucid-logbook repo

Implements `POST/DELETE /api/v1/auth/apikey` returning self-signed JWTs (recommended) or DB-backed keys (acceptable). Per spec: storage is a logbook-internal choice; the wire protocol is what's uniform.

Deploy to `bcglucidlogbook.dhcp.lbl.gov`. Test the contract endpoint-by-endpoint.

### 6. LUCID logbook consumer migration — separate plan in ncs/ncs

Switches `lucid/logbook/client.py` from `SessionAuth` to `ServiceKeyAuth("logbook")`. Adds `logbook` to the services that `_mint_all_service_keys` mints at login. Updates `mint_service_key` call sites to include logbook scopes (`[]`).

Tests cover the new path. Refresh machinery still alive (logbook hasn't gone away from the bearer in any non-`SessionAuth` consumer — verify the grep is clean here).

### 7. LUCID auth cleanup — separate plan in ncs/ncs

The end-state plan, written last:

- Delete `SessionManager._schedule_refresh`, `_do_scheduled_refresh`, `_on_refresh_success`, `_on_refresh_failure`, `_get_jwt_exp`, `_start_single_shot`, `_cancel_refresh_timer`, `_on_state_for_refresh`. Narrow the `timerEvent` deletion to the refresh timer specifically.
- Delete `_refresh_in_progress`, `_fast_retry_count`, `_refresh_timer_id` state fields.
- Make `_mint_all_service_keys` discard `session.token` / `session.refresh_token` / `session.id_token` after success, preserving `id_token` on `SessionManager._id_token_for_logout`.
- Update `KeycloakAuthProvider.logout` to read `id_token` from the new slot.
- Add the logout-RE-gate UI: if `engine.RE.state in {"running", "paused"}`, show confirm dialog citing data-write loss. Wire into the existing logout action.
- Delete `lucid.auth.httpx_auth.SessionAuth` (no consumers left).
- Final integration test against bcgtiled + bcglucidlogbook: full round-trip from login → Tiled read → logbook write → logout (idle) → logout (RE active, confirm).

After this plan merges, the spec's stated end state is reached.

---

## Cross-cutting concerns

### Backwards compatibility during rollout

Each plan keeps the system green:
- After step 2: Tiled goes through API keys; logbook still uses bearer. Bearer is still alive in `SessionManager`. Existing refresh keeps working — no regression for logbook.
- After step 5: logbook server accepts both bearer and API-key during its own transition window if the logbook implementation chooses (the logbook plan decides). Once its consumers migrate (step 6), the bearer path on the logbook server can be removed in a follow-up to the logbook plan.
- After step 6: nothing reads `session.token` in LUCID. Refresh machinery is dead code waiting for step 7 to delete it.
- After step 7: refresh machinery gone. Bearer discarded post-mint. End state reached.

### Atomic-cutover rule (from spec)

Each service migrates with its consumers. Concretely:
- Tiled: server (Stage 0, done) + LUCID consumers (LUCID core plan).
- Logbook: server (logbook plan) + LUCID consumers (LUCID logbook consumer plan). The logbook plan should ship the server-side change in a feature branch and only flip to API-key-required after the LUCID consumer plan merges.
- Pipelines + tsuchinoko: their executors already speak API keys; only their LUCID-side dispatchers change, plus the NATS payload field rename. These are one-PR each.

### Deployment surface

The deployments touched by this rollout:
- `bcgtiled:/opt/als-tiled` — already updated for Stage 0.
- `bcglucidlogbook.dhcp.lbl.gov` — logbook server deploy in step 5.
- LUCID workstations — picked up via `pip install -e .` from the LUCID master branch; no special deploy.
- Pipelines executor — picked up on next `pip install` of `lucid-pipelines`.
- Tsuchinoko executor — picked up on next deploy of the tsuchinoko service.

### Rollback strategy

If any per-repo plan is reverted, the previous plan's state should be a valid system. The key invariants:

| Reverted | Prior valid state                                                    |
| --- | --- |
| LUCID core plan       | Old refresh-based bearer flow for Tiled. Plan A's apikey endpoint is unused but harmless. |
| Pipelines / tsuchinoko payload migration | Old `mint_job_key` per-job mint path. (Plans are tiny; reverts are trivial.) |
| Logbook server plan   | Logbook bearer auth still works. LUCID logbook consumer plan must NOT have merged yet for this to be safe. |
| Logbook consumer plan | LUCID logbook client returns to `SessionAuth`. Logbook server's apikey path is unused. |
| Auth cleanup plan     | Refresh machinery restored from git; bearer-survives-the-session restored. The migration to API keys remains for the services that already moved. |

---

## Testing strategy

Each per-repo plan owns its own unit + contract tests. The cross-cutting integration suite lives in `ncs/ncs/tests/integration/` and is added/updated in each LUCID-side plan:

- `test_auth_v2_login_mints_tiled.py` — added in LUCID core plan; verifies the Tiled key is minted at login and used on subsequent calls.
- `test_auth_v2_login_mints_logbook.py` — added in LUCID logbook consumer plan; verifies the same for logbook.
- `test_auth_v2_full_roundtrip.py` — added in LUCID cleanup plan; end-to-end across both services.
- `test_auth_v2_logout_re_gate.py` — added in LUCID cleanup plan; verifies the dialog and confirm flow.

---

## Open questions resolved during rollout

The spec lists implementation-time questions. Each per-repo plan should pick them up:

1. **Mint thread composition with QThreadFuture** — answered in the LUCID core plan.
2. **`get_minted_key` vs. `get_api_key`** — answered in the LUCID core plan.
3. **`LucidConfig.services` shape** — answered in the LUCID core plan. (Likely just reads existing `tiled_url` / `logbook_url` preferences.)
4. **Executor `StaticApiKeyAuth`** — answered in the LUCID core plan (exporter half).
5. **lucid-logbook key storage** — answered in the lucid-logbook plan.

---

## Completion criteria

- [ ] LUCID core plan merged, Tiled fully migrated, regression suite green.
- [ ] Pipelines + tsuchinoko payload migrations merged (in parallel).
- [ ] lucid-logbook mint endpoint shipped + deployed.
- [ ] LUCID logbook consumer plan merged.
- [ ] LUCID auth cleanup plan merged.
- [ ] `grep -rn 'session\.token' src/lucid/` returns nothing outside the Keycloak provider's mint window.
- [ ] `grep -rn 'SessionAuth\|KeycloakTiledAuth' src/lucid/` returns nothing.
- [ ] `git log --oneline --all --grep='refresh'` shows the deletion commit.

Once all boxes are ticked, the spec's stated end state is reached and this index can be moved to `docs/superpowers/specs/archive/` along with the spec.
