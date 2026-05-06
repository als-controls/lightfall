# lucid-deck — Companion-driven motor control surface for LUCID

- **Date:** 2026-05-05
- **Status:** Design (pre-implementation)
- **Repo:** new — `lucid-deck` (sibling of `lucid-endstation-*`, `lucid-dev-plugins`)

## Summary

A new repo, `lucid-deck`, that lets a Bitfocus Companion deck (typically running on a phone, virtual MCP Deck, or any other Companion surface) act as a hardware control surface for Ophyd positioners exposed by LUCID. A LUCID-side plugin publishes motor actions and readback over NATS using LUCID's existing `IPCService`. A separate `companion-bridge` console-script translates between Companion's HTTP and that NATS bus, and pushes live state into Companion custom variables. AI authoring of the Companion layout is done via the existing `companion-mcp-server` (already integrated into Claude Code at the user level); no LUCID-side AI tooling is included in this spec.

## Goals

- Use Companion as a touch/button motor control surface for a configurable subset of LUCID's catalogued positioners.
- Top-level page: motor selectors. Drill into a single shared "Motor Control" page driven by a `selected_motor` Companion variable.
- Step-on-press jog with a global step size scaled by `×10` / `÷10` buttons. Absolute move via a number-pad page. Stop button.
- Live readback (position, units, limits, moving, at-limit flags) pushed into Companion variables for display and feedback.
- Match LUCID's established "headless service over NATS" pattern (mirrors the exporter).

## Non-goals

- Continuous (hold-to-jog) motion. Step-on-press only in v1; step size is the affordance for fine vs coarse control.
- Authentication on the bridge — local-only deployment; trust boundary is the host/tailnet.
- A LUCID-side AgentPlugin / MCP for direct AI motor control. Separable feature; will be its own spec if pursued.
- A custom LUCID Qt motor widget (`ControllerPlugin`). Not required by this design.
- Multiple bridges or multiple Companion targets. Single-bridge, single-Companion in v1.
- Hardware-in-the-loop tests in CI.

## Architecture

```
   ┌──────────────────────┐  NATS subjects:           ┌────────────────┐  HTTP request:                ┌───────────────┐
   │  LUCID main process  │   lucid.motors.action.*   │  companion-    │   POST /select?name=samx      │  Companion    │
   │  ────────────────    │ ◀──────────────────────▶  │  bridge        │ ◀──────────────────────────── │  (Generic     │
   │ • MotorsSettingsPlug │   lucid.motors.readback.* │  ────────────  │                               │  HTTP module) │
   │   (catalog + config) │   (publishes always)      │ • FastAPI      │   HTTP push:                  │               │
   │ • motor_actions.py   │                           │ • nats subscr. │   POST /api/custom-variable/  │  buttons fire │
   │   (NATS handlers,    │                           │ • Companion    │   selected_motor.position/    │  HTTP, render │
   │    readback pub)     │                           │   client       │   value (and friends)         │  vars in text │
   │ • Ophyd objects      │                           │ ────────────▶  ────────────────────────────────▶               │
   └──────────────────────┘                           └────────────────┘                               └───────────────┘
```

Key properties:

- **NATS is the trust boundary** between LUCID and the bridge. LUCID never holds an HTTP server.
- **Bridge ↔ Companion** is bidirectional HTTP: Companion calls the bridge for actions; the bridge pushes Companion variable updates as readback ticks arrive on NATS. No polling.
- **LUCID side is stateless about selection** — every NATS action carries `{name, …}`. Bridge owns `selected_motor` in memory.

## Components

### 1. LUCID plugin package (loaded into LUCID's main process)

Lives in `lucid-deck`'s Python package, imported via a `PluginManifest` entry registered with LUCID's plugin loader.

#### `MotorsSettingsPlugin` (`SettingsPlugin`)

Preferences-dialog page. Configures:

- **Selected motors**: multi-pick from `catalog.list_devices(category=DeviceCategory.MOTOR)`. The picked subset is the universe exposed to the bridge.
- **Step values**: a single global ordered list of step sizes (e.g., `[0.001, 0.01, 0.1, 1.0, 10.0]`). The current step value lives in Companion as `motor_step`; LUCID does not own it. The list seeds Companion's initial value and bounds for the `×10` / `÷10` scale buttons.

The bridge process is configured independently via env vars — LUCID's SettingsPlugin does not pipe configuration to it (they are different processes). Keeping `lucid-deck`-specific config minimal in the SettingsPlugin avoids duplicating bridge-process settings that LUCID would never read.

`on_loaded()` registers the NATS action handlers (`motor_actions.register(ipc_service)`) and starts the readback publisher. `save_settings()` re-validates the motor list against the live catalog.

#### `motor_actions.py`

NATS action handlers, using the existing `IPCService`:

| Subject | Payload | Reply |
|---|---|---|
| `lucid.motors.action.list` | `{}` | `{ok, motors: [name, …]}` |
| `lucid.motors.action.status` | `{name}` | `{ok, status:{position, units, high_limit, low_limit, at_high, at_low, moving}}` or `{ok:false, code, msg}` |
| `lucid.motors.action.select` | `{name}` | `{ok, status:{…}}` (returns full status so bridge can populate vars in one round-trip) |
| `lucid.motors.action.jog` | `{name, delta}` | `{ok, expected_setpoint}` or error |
| `lucid.motors.action.move` | `{name, position}` | `{ok, expected_setpoint}` or error |
| `lucid.motors.action.stop` | `{name}` | `{ok}` or error |

All handlers wrap in `try/except`; structured errors are returned as `{ok:false, code, msg}`. Codes: `unknown_motor`, `at_limit`, `hardware_error`, `internal`.

Action semantics:

- `jog` → `motor.move(motor.position + delta)` (Ophyd-relative; equivalent to `mvr`).
- `move` → `motor.move(position)`.
- `stop` → `motor.stop()` (Ophyd halt; not a hardware E-stop).
- All return as soon as motion is *dispatched*, not when it settles. Settle/intermediate state propagates via readback events.

#### Readback publisher

For each enabled motor, subscribe to Ophyd's `subscribe()` callback. On each update, publish on `lucid.motors.readback.<name>` with payload `{position, units, high_limit, low_limit, at_high, at_low, moving, ts}`. `moving` and `at_high`/`at_low` are derived from Ophyd signals (`.moving`, `.high_limit_switch`, `.low_limit_switch` or equivalent) — not from position-delta heuristics.

When the configured motor list changes (via SettingsPlugin save), tear down stale subscriptions and add new ones.

### 2. `companion-bridge` console-script (separate process)

Same repo, registered as a `[project.scripts]` entry point.

- **FastAPI app** with endpoints:
  - `POST /select?name=…` — sets bridge's `selected_motor`, NATS `select`, pushes all `motor_*` vars from the returned status.
  - `POST /jog?delta=…` — NATS `jog` against `selected_motor`. Accepts negative deltas.
  - `POST /move?position=…` — NATS `move` against `selected_motor`.
  - `POST /stop` — NATS `stop` against `selected_motor`.
  - `GET /status` (debug) — returns bridge's view of selection + most recent readback.
- **NATS client** (nats-py): subscribes to `lucid.motors.readback.<selected_motor>`. On selection change, re-subscribes (or filters in-process). On each tick, push relevant `motor_*` Companion variables via `POST <COMPANION_URL>/api/custom-variable/<name>/value`.
- **Companion HTTP client** (httpx): used for variable push. Fire-and-forget; on failure, log and drop (push is idempotent — the next readback supersedes).
- **Configuration** via env vars or CLI flags: `NATS_URL`, `COMPANION_URL`, `BIND_HOST` (default `127.0.0.1`), `BIND_PORT` (default `8765`).
- **Selection state**: in-memory only. Survives nothing across restart. After a bridge restart, Companion buttons keep working; vars stay stale until next `select`.

### 3. Companion layout (data, not code in this repo)

Authored ad-hoc via the existing `companion-mcp-server` MCP integration in Claude Code. Not generated or maintained by `lucid-deck` itself. The layout this design assumes:

**Custom variables (global):**

| Variable | Type | Source | Notes |
|---|---|---|---|
| `motor_selected` | string | bridge (push) | name of currently selected motor; empty before first select |
| `motor_position` | string | bridge (push) | formatted position with units stripped |
| `motor_units` | string | bridge (push) | EGU |
| `motor_high_limit` | string | bridge (push) | |
| `motor_low_limit` | string | bridge (push) | |
| `motor_at_high` | bool/0-1 | bridge (push) | for button feedback styling |
| `motor_at_low` | bool/0-1 | bridge (push) | for button feedback styling |
| `motor_moving` | bool/0-1 | bridge (push) | |
| `motor_step` | number | Companion (local) | current jog step; bridge never reads or writes this |
| `motor_status_msg` | string | bridge (push) | latest error or empty on success |

**Pages:**

- *Top page (Motors)*: one button per configured motor. Each button does (a) `POST /select?name=<motor>` (Generic HTTP module) and (b) page-jump to "Motor Control".
- *Motor Control page*: name display (`$(custom:motor_selected)`), readback (`$(custom:motor_position) $(custom:motor_units)`), jog `+` and `−` buttons (`POST /jog?delta=$(custom:motor_step)` and `…?delta=-$(custom:motor_step)`), `×10` and `÷10` step-scale buttons (update `motor_step`), `Move…` button (jumps to a number-pad page that ends with `POST /move?position=<entered>`), `Stop` button (`POST /stop`), `Back` button. Status text reads `$(custom:motor_status_msg)`. Limit-arrival shown via button feedback comparing `motor_at_high`/`motor_at_low`.

## Data flows

### A. Select motor
1. User taps `samx` on top page → Companion fires `POST /select?name=samx` then page-jumps to Motor Control.
2. Bridge issues NATS `lucid.motors.action.select {name:samx}` → LUCID validates, returns `{ok, status:{…}}`.
3. Bridge sets `selected_motor=samx`, pushes all `motor_*` Companion variables from the returned status, clears `motor_status_msg`.
4. Bridge ensures NATS subscription is active for `lucid.motors.readback.samx`.

### B. Jog
1. User taps `+`. Companion fires `POST /jog?delta=$(custom:motor_step)`.
2. Bridge: NATS `action.jog {name:selected_motor, delta}` → LUCID `mvr`. Returns `{ok, expected_setpoint}` immediately.
3. Motion publishes readback ticks on NATS; bridge pushes updates to Companion variables. `motor_moving` flips true → false at end-of-motion.

### C. Stop
1. User taps `Stop`. `POST /stop`.
2. NATS `action.stop` → `motor.stop()`. Readback flips `motor_moving=false`.

### D. Readback when no motor is selected
- Bridge ignores all readback subjects (subscribed only for the selected motor).
- Companion's `motor_*` vars retain stale values from the last selection until next selection. (Optional follow-up: clear vars on bridge startup.)

## Error handling

A single `motor_status_msg` Companion variable surfaces errors to the user. The Motor Control page shows it as a small text region. Successful actions clear it.

| Failure | LUCID side | Bridge side | User-visible |
|---|---|---|---|
| Motor name unknown / disabled | `{ok:false, code:"unknown_motor"}` | HTTP 404, push `motor_status_msg` | "Motor not configured" |
| At limit, jog rejects | `{ok:false, code:"at_limit"}` | HTTP 409, push `motor_status_msg` | "At high limit" |
| Ophyd raises | caught → `{ok:false, code:"hardware_error", msg}` | HTTP 502, push `motor_status_msg` | hardware error msg |
| NATS request times out | — | HTTP 504, push `motor_status_msg` | "LUCID not responding" |
| NATS unreachable at startup / runtime | — | nats-py reconnects with backoff; HTTP 503 until connected | buttons inert; "no LUCID" status |
| Companion unreachable for variable push | — | log + drop | vars stale until Companion back |
| Catalog change disables current motor mid-session | next action returns `unknown_motor` | propagates as 404 | "Motor no longer configured" |

Resilience:

- NATS auto-reconnect via nats-py defaults.
- Variable push is fire-and-forget with a short timeout. No queue — next readback supersedes.
- All NATS handlers wrap in `try/except`; never raise across the wire.

Idempotence:

- `select` is idempotent. `jog`/`move` are not; replay sends another relative move. Accepted as honest motor semantics.

## Testing strategy

Three test surfaces:

1. **LUCID-side action handlers.** Unit tests against a synthetic Ophyd positioner (controllable `position`, `high_limit_switch`, `moving`). Use `lucid/devices/backends/mock.py` motor mocks for the catalog. Assert handlers return the right shape on success and each error path. Integration test through `IPCService` against a local nats-server (the same harness used for the exporter).
2. **Bridge process.** FastAPI `TestClient` for HTTP endpoints with NATS mocked at the boundary. Separate unit test for the readback-translation path: feed a fake readback event, assert the right `POST /api/custom-variable/...` is emitted (mock httpx). End-to-end happy-path test: real nats-server + a fake LUCID action responder + bridge under test + assertions on emitted Companion calls.
3. **Companion layout.** Config-as-data, smoke-tested manually for v1. The companion-mcp-server can be used by Claude to introspect generated buttons/triggers ad-hoc; not a CI gate.

Tooling alignments:

- `pyproject.toml` with hatch + hatch-vcs, pytest, ruff. Standard LUCID Python conventions.
- `lucid-deck`'s `.venv` has lucid installed editable: `pip install -e ../ncs/ncs`.
- Tests run with `.venv/Scripts/python -m pytest` (Windows) / `.venv/bin/python -m pytest` (Linux).
- No CI in v1 — match the lucid-endstation-* pattern.

Out of scope for tests in v1:

- Real Companion instance in CI.
- Hardware-in-the-loop.

## Implementation details deferred to plan time

- **Step-scale button mechanism**: whether `×10` / `÷10` buttons update `motor_step` natively in Companion (using whatever variable arithmetic Companion's actions support) or punt through a small `POST /scale?factor=10` on the bridge that pushes the variable back. Spec commits to "step lives in Companion." Implementation chooses the cleanest mechanism that exists today.
- **Number-pad page** for `Move…`: Companion-native digit collection vs. bridge endpoint to assemble a number. Not architecturally meaningful.
- **`motor_at_high` / `motor_at_low` source**: Ophyd `EpicsMotor` exposes these directly; for `PositionerBase` subclasses without explicit limit-switch signals, derived from `position ≥ high_limit`. Per-positioner-class detail.
- **Multi-process startup orchestration**: how `companion-bridge` is launched relative to LUCID. Recommendation in plan: a systemd user unit / Windows scheduled task / just-run-it-by-hand. Not specified here.
- **NATS server location**: same NATS LUCID already uses for IPC. Bridge connects to the URL LUCID is configured for (read from LUCID's IPC config or supplied to the bridge as env var).

## Future work / explicitly deferred

- LUCID-side `AgentPlugin` exposing `mcp__lucid_deck__*` tools so Claude can drive motors directly outside Companion. Separate spec.
- Hold-to-jog (continuous motion until release). Requires a heartbeat in the bridge to handle dropped `up` events safely. Separate spec.
- A LUCID Qt motor widget (`ControllerPlugin`) that mirrors the Companion controls in the desktop UI. Separate spec.
- Multi-Companion targeting (push variables to multiple Companion instances simultaneously). Separate spec.
- Authentication on the bridge for non-local deployments.
- Persisting `selected_motor` across bridge restarts.
- Layout regeneration tooling (a Claude command/recipe that re-runs the companion-mcp-server flow when the configured motor list changes).
