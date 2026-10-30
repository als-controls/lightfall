# Settings layering: deployment config vs. user cache

**Status:** implemented in #52 (2026-10-29): union `device_backends`, console layer, cache moved to `scope-cache/`, migration only on unbound machines
**Date:** 2026-10-29
**Trigger:** device backend rows configured at 12.0.2.1 silently replaced this
console's rows at 7.0.1.1 (see "The incident" below).

## Summary

`preferences.*` in `application.yaml` serves two unrelated purposes at once: it
is the deployment's own configuration, and it is the logged-in user's scope
cache. Any key that resolves from a user's server scope is written through into
that same slot, destroying the deployment value with no copy retained.

Separately, `device_backends` has no `SettingSpec`, so it defaults to
`scopes={"user"}` — the device table is a personal, deployment-independent
setting that follows an account between beamlines.

The two defects compound: a mis-scoped key is not merely resolved wrongly, it is
written through and the local value is lost permanently.

## The incident

Evidence from the 7.0.1.1 console (`~/.config/ncs/`):

| Artifact | Contents |
|---|---|
| `scope-cache/user-7b98c28c….json` (ronpandolfi) | 6 backend rows incl. `C:/Users/rp/Downloads/replica-….tar`, `C:\Users\admin\lightfall\happi\bcs_12021_chipps.json`; `ipc_topic_prefix: als.12021`; `tiled_beamline: 12.0.2.1` |
| `scope-cache/user-b5d134ec….json` (smorley) | 2 rows (mock, happi `/home/alsuser/static.json`) |
| `application.yaml` | `_scoped_cache_owner: smorley`, `_scoped_migrated: [ronpandolfi, smorley]`, `device_backends` = smorley's 2 rows |

Sequence:

1. Rows configured at 12.0.2.1 are stored at `user:7b98c28c` on the server,
   because `device_backends` is undeclared and defaults to user scope.
2. ronpandolfi logs in at 7.0.1.1. The chain resolves `device_backends` from his
   user scope. `ScopedBackend._sync_yaml` (`scoped_backend.py:457`) writes the
   resolved value through `_cache_yaml` → `LocalPreferenceBackend.store_quiet`
   → `preferences.device_backends`. **The deployment's rows are overwritten in
   place.** Nothing retains them.
3. On that same first login, `_scoped_cache_owner` was absent, so
   `_yaml_foreign()` returned False and `_after_online` ran `migrate_user_prefs`,
   uploading this console's YAML prefs into his user scope. Existing keys 412 and
   are skipped, so his 12.0.2.1 rows won; keys he lacked were absorbed. This is
   why `/home/alsuser/static.json`, a 7.0.1.1 path, now sits in his personal
   scope beside the Windows ones.
4. smorley logs in. `_claim_yaml` (`scoped_backend.py:424`) sees
   `owner != smorley`, purges keys absent from her scope, sets owner, and
   `_sync_yaml` writes her 2 rows. It restores nothing — the deployment rows
   died at step 2.

The `_yaml_foreign()` guard worked as designed. It prevents one user *reading*
another's cache; it does not stop either from destroying the deployment's own
values, because both live in the same key.

### Wider exposure

`tiled_beamline` and `ipc_topic_prefix` *are* declared `_BEAMLINE_CAPABLE`
(`scopes/keys.py:46`), but `resolve()` walks broad→specific with
`KIND_RANK = {facility:0, beamline:1, branch:2, user:3}` and policy `override`,
so a user-scope value beats the console's branch scope. While ronpandolfi was
logged in at 7.0.1.1, that console resolved `tiled_beamline = 12.0.2.1` and
`ipc_topic_prefix = als.12021` — reading the wrong catalog and publishing to the
wrong IPC topic. smorley's takeover purged both (`null` today); they return on
his next login. This is more operationally dangerous than the device rows and is
*not* fixed by declaring `device_backends`.

## Requirement

Device rows must be configurable at three levels:

- **beamline** — the shared, provisioned configuration for the beamline.
- **local** — this specific console. Primarily diagnostic work.
- **user** — personal. No concrete use case yet, but in scope.

## Current architecture

| Layer | Backend | Storage | Active when |
|---|---|---|---|
| Server scope chain: facility → beamline → branch → user | `ScopedBackend` | logbook server, cached in `scope-cache/` | logged in |
| Facility/beamline/branch from files | `LocalScopesBackend` | `/etc/ncs/scopes/*.yaml` (`%PROGRAMDATA%/ncs/scopes` on Windows), read-only | server off / offline |
| Everything else **+ the user's scope cache** | `LocalPreferenceBackend` | `application.yaml` `preferences.*` | always |

There is no machine-level *writable* layer. `/etc/ncs/scopes` is read-only and
models facility/beamline/branch, not "this console".

## Constraint: `ScopeKind` is not locally extensible

`ScopeKind` is `Literal["facility","beamline","branch","user"]` in
`lightfall_settings` — a separately published package (0.1.3, not an editable
install), and the server enforces specs against the same vocabulary. Adding a
`machine` kind is a cross-repo change to the package *and* the server. A
client-side layer avoids that.

## Proposal

### Part 1 — Separate the user cache from deployment config

The scope cache stops living in `preferences.*`. Give it its own namespace, so
the deployment's configuration is never the thing being overwritten.

- `LocalPreferenceBackend.store_quiet()` currently maps to
  `preferences.<key>` via `_config_key()` (`backend.py:114-127`). Route the
  scoped cache to a distinct root instead — e.g. `scope_cache.<identity>.<key>`
  — or drop it from the YAML entirely and keep it in `scope-cache/`, which
  already stores per-user snapshots and is already keyed by identity.
- `preferences.*` then means exactly one thing: this console's own settings.
- `_claim_yaml`/`_scoped_cache_owner` become unnecessary for cache isolation;
  per-identity namespacing gives it structurally.

This is the load-bearing change. Without it, *any* mis-scoped key is destructive,
and the three-level model below cannot have a durable local layer.

### Part 2 — A writable local layer

With `preferences.*` no longer doubling as cache, it becomes the local layer.
Resolution for a key that allows it:

```
beamline/branch (scope chain)  →  local (preferences.*)  →  user (scope chain)
```

placing local between branch and user. This is the ordering that follows from
specificity, and it keeps the existing precedent that a user's own value wins.

**Decided (Ron, 2026-10-29): user wins, and diagnostics live at user scope.**
Local is *not* the diagnostic layer. Beamline consoles commonly share one OS
account, so a console-level diagnostic row is visible to whoever logs in next —
the precise leak this document exists to stop. Diagnostics are individual and
must not escape the individual.

This gives `beamline → local → user`, plain specificity ordering, no locks on
the local layer. The layers mean:

- **beamline/branch** — the provisioned configuration for the beamline.
- **local** — genuinely machine-specific wiring that is true no matter who is
  logged in: filesystem paths, BCS host/port, a backend that only exists on this
  box. Compare `device_happi_path`, already in `LOCAL_ONLY_KEYS`.
- **user** — personal, including diagnostic rows. Follows the account, visible
  to nobody else.

An earlier draft proposed expressing diagnostic pinning as a `Lock` on the local
layer. That is rejected: it makes one person's diagnostic session override
everyone else's settings on a shared console, and locks are a staff-provisioning
tool, not a per-user one.

Note this makes Part 1 strictly necessary rather than merely desirable. User
values win, so they are written through on every login; until the cache has its
own namespace, every login keeps overwriting the local layer.

### Part 3 — Declare `device_backends`

```python
SettingSpec(
    key="device_backends",
    scopes=frozenset({"beamline", "branch", "user"}),
    write_role="maintainer",
)
```

plus `device_backends_exclusive_restore`, which is UI bookkeeping for the rows
and must not diverge from them.

Note that declaring it does **not** by itself fix the incident: user scope still
wins at `KIND_RANK` 3. It is Part 1 that stops the overwrite and Part 2 that
makes the beamline value survivable.

**Consider `policy="union"` with `subtractable=True`.** `resolve()` already
implements per-item merging for `union` keys, tracking `item_origins` per item
and treating an item set to `None` as a removal. If rows were stored as a
mapping keyed by row `id` rather than a list, a beamline could provision a base
set while a console adds a diagnostic row and a user disables one — each layer
contributing rows instead of replacing the whole table. That is a closer fit to
the three-level requirement than `override`, which makes any layer's edit
discard every row from the layers above it. It requires a storage shape change
(`list[dict]` → `dict[id, dict]`) and a migration in
`devices/backend_types/rows.py`.

### Part 4 — Write path

`save_rows()` (`rows.py:145`) calls `prefs.set(DEVICE_BACKENDS_KEY, rows)` with
no scope, and `ScopedBackend.set()` routes an unscoped write to the user scope
unconditionally. With three levels the Device settings page must choose a target
level, which means:

- a scope selector in the device settings UI, and
- `write_role` enforcement — `maintainer` for beamline/branch — so an operator
  editing rows writes locally rather than failing against the server.

Without this, declaring the key narrower than `{"user"}` makes the page appear
to save while `resolve()` discards the value.

### Part 5 — Remediation

Already-polluted user scopes need cleaning; a correct client will otherwise keep
resolving the bad values. At minimum: delete `device_backends`,
`tiled_beamline`, and `ipc_topic_prefix` from `user:7b98c28c-d01e-436b-af3a-4e01799d54ae`,
and audit other user scopes for keys absorbed by `migrate_user_prefs`.

Also reconsider `migrate_user_prefs` itself: uploading a shared console's YAML
into the first user's personal scope is how machine-specific paths become
personal settings. It should at minimum skip keys that are beamline-capable.

## Decisions needed before a plan

1. ~~Local vs. user precedence (Part 2)~~ — **decided**: `beamline → local →
   user`, diagnostics at user scope, no local locks.
2. `override` vs. `union` for the row table (Part 3) — recommendation: `union`,
   accepting the storage migration.
3. Where the scope cache lives (Part 1) — `scope_cache.<identity>.*` in the YAML,
   or move it out to `scope-cache/` entirely.
4. Whether `migrate_user_prefs` should run on shared consoles at all (Part 5).

An implementation plan follows once 2–4 are settled; the sequencing is
Part 1 → Part 2 → Part 3/4 → Part 5, and Part 1 is independently shippable and
independently valuable.
