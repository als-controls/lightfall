# LUCID Autonomous Experiments — Embedded-Agent Integration

**Status:** Draft
**Date:** 2026-05-19
**Authors:** Ron Pandolfi, Ayaka
**Repos affected:** `ncs/ncs` (LUCID), `tsuchinoko` (`LUCID-refactor` branch)

## Summary

LUCID already ships every load-bearing piece of an autonomous-experiment
demo: the `adaptive_experiment` Bluesky plan with its `bind_run`
handshake, the `AdaptiveHeatmapVisualization` and
`AdaptiveHyperparameterPlot` widgets that read from the `adaptive`
Tiled stream, the IPC service and NATS bus, and an embedded Claude
agent that can run plans via `ncs_run_plan`. Tsuchinoko's
`LUCID-refactor` branch exposes a stable `tsuchinoko.*` NATS surface
(`experiment.{configure, bind_run, start, pause, resume, stop}`,
`engine.{set_parameter, get_parameters}`, `status`, events
`state / targets / gp.updated / error`, discovery via
`_tsuchinoko.discover`).

The connective tissue is missing in one place only: the embedded
agent has no knowledge of Tsuchinoko, no exposure to the gpCAM
experiment-designer skill, and no MCP tools that drive the
`tsuchinoko.*` NATS surface. A second, smaller gap exists on the
Tsuchinoko side: `experiment.configure` accepts only
`parameter_bounds` today, which is too thin to land the gpCAM
designer skill's output (kernel choice, acquisition function, prior
mean, noise model, hyperparameter initialisation, training method,
multi-task indices). User-authored Python callables (custom
acquisition functions, kernels, etc.) cannot cross the process
boundary at all.

This spec closes both gaps with the minimum cross-repo footprint:

1. **LUCID** gains one AgentPlugin (`AutonomousExperimentAgent`) that
   carries a short stub prompt, lazy-loads the gpCAM skills tree as
   SDK references, and exposes five MCP tools over the existing
   `tsuchinoko.*` NATS surface.
2. **Tsuchinoko** extends `experiment.configure` into a typed payload
   and adds one new action, `experiment.upload_design_code`, which
   lands agent-authored Python into a per-user `user_designs/`
   directory and exposes it via `"user:<name>"` refs.

After both land, an end-to-end demo flows entirely through the
embedded agent: design with gpCAM's skills, upload any custom
callables, configure Tsuchinoko, run the existing
`adaptive_experiment` plan, watch the existing adaptive viz widgets
update live.

## Goals

- Enable an end-to-end "design → run → display" autonomous-experiment
  demo driven by the LUCID embedded agent, with no new orchestration
  plan and no new visualisation widgets.
- Keep gpCAM as the single source of truth for experiment-design
  knowledge — the LUCID plugin must not duplicate skill content.
- Preserve the existing `tsuchinoko.*` NATS contract; do not introduce
  parallel topics or new processes.
- Make the cross-repo dependency explicit and orderable: Tsuchinoko
  side merges first, LUCID side merges second; either side can be
  rolled back independently.

## Non-goals

- A new dock panel for autonomous-experiment control. The embedded
  agent + Settings → IPC are sufficient for the demo; a dock panel
  is a separate spec if/when we want it.
- Multi-instance routing. The agent's `tsuchinoko_discover` tool
  returns all responders, but per-instance addressing (`tsuchinoko.<id>.*`)
  is out of scope. The demo assumes one Tsuchinoko per beamline.
- A LUCID-managed Tsuchinoko subprocess. Tsuchinoko runs externally
  (systemd, a pinned dev terminal, or `tsuchinoko run` invoked
  manually); LUCID only discovers and talks to it.
- Per-action ACLs over NATS. The trust boundary stays exactly where
  the existing IPC design put it.
- Changes to `bind_run`, `LUCIDEngine`, `TiledPublisher`, the
  `adaptive_experiment` plan body, or the adaptive viz widgets.

## Background

The IPC bus is settled (spec `2026-04-09-ipc-design.md`). The SDK
plugin model is settled (spec `2026-04-25-lucid-sdk-native-plugins-design.md`);
`AgentPlugin` is the single extension point that contributes both
a skill prompt and an in-process MCP server. Tsuchinoko's rescope to
a headless NATS service is documented in
`tsuchinoko/docs/design/2026-04-12-tsuchinoko-rescope.md` and its
phase-2 NATS integration plan; the `LUCID-refactor` branch holds the
implementation. The `adaptive_experiment` plan and the two adaptive
visualisations are already on master in `ncs/ncs`.

gpCAM lives at `~/PycharmProjects/gpcam` and ships eight Claude skills
under `gpcam/skills/`:

- `experiment-designer/` (entry point)
- `acquisition-functions/`
- `cost-functions/`
- `gp2scale-advanced/`
- `kernel-designer/`
- `multi-task-advanced/`
- `noise-functions/`
- `prior-mean-functions/`

Several of these author Python callables (e.g. a custom UCB with a
data-dependent `beta`, a periodic-plus-Matérn kernel, a noise
function that depends on detector counts). That capability is the
hard reason `experiment.configure` cannot stay a thin
canned-choices schema.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  LUCID (ncs/ncs, branch feature/autonomous-experiment-agent)    │
│  ─────────────────────────────────────────────────────────────  │
│  Embedded Claude agent                                          │
│    └─ NEW: AutonomousExperimentAgent (AgentPlugin)              │
│         • get_system_prompt() → short stub                      │
│         • get_references_dir() → gpcam.skills/ (lazy)           │
│         • create_tools() → 5 NATS-bridge MCP tools              │
│  Existing: adaptive_experiment plan, adaptive viz widgets       │
└────────────────────┬────────────────────────────────────────────┘
                     │  NATS  tsuchinoko.*   (existing trusted bus)
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│  Tsuchinoko (LUCID-refactor branch, +small MR)                  │
│  ─────────────────────────────────────────────                  │
│  Existing: NATSService, Core, LUCIDEngine, TiledPublisher       │
│  CHANGED: experiment.configure  — typed payload, strict valid.  │
│  NEW:     experiment.upload_design_code                         │
│  NEW:     ~/.tsuchinoko/user_designs/<kind>/<name>.py           │
└─────────────────────────────────────────────────────────────────┘
```

No new processes. No new topics. No new viz widgets.

## End-to-end demo flow

1. **User** asks the embedded agent for an autonomous scan
   ("smart scan motors x,y, maximise det1, about 50 points").
2. **Agent** loads gpCAM's `experiment-designer` skill from the
   lazy-references dir, optionally pulling in sibling skills
   (`acquisition-functions`, `kernel-designer`, …) as needed.
3. **Agent** calls `tsuchinoko_discover()`; takes the first
   responding instance.
4. **Agent** (if the design needs a custom callable) calls
   `tsuchinoko_upload_design_code(name, kind, code)` once per
   callable. Each call returns a ref string of the form
   `"user:<name>"`.
5. **Agent** calls `tsuchinoko_configure(payload)` with the typed
   design schema, substituting ref strings where appropriate.
6. **Agent** calls `ncs_run_plan(plan_name="adaptive_experiment",
   params={"detectors": [...], "motors": [...], "timeout": ...})`.
7. **LUCID plan** opens a single Bluesky run, sends
   `tsuchinoko.experiment.bind_run` with Tiled credentials and the
   `run_uid`. Tsuchinoko's existing handler auto-transitions to
   `Starting`, wires `TiledReader` and `TiledPublisher`, and starts
   pushing targets on `tsuchinoko.targets`.
8. **LUCID plan** moves motors, reads detectors, publishes
   `{lucid_prefix}.adaptive.measured` after each measurement (or
   each batch, per the `exhaust_first` knob).
9. **Tsuchinoko** writes the `adaptive` stream incrementally — one
   sub-container per iteration (`adaptive/iter_NNN/`) — into the
   same Tiled run.
10. **User** opens the Visualization Panel. `AdaptiveHeatmapVisualization`
    and `AdaptiveHyperparameterPlot` discover the run by its
    `tsuchinoko.experiment_id` metadata and refresh live as new
    iterations land.
11. **Agent** answers progress questions via `tsuchinoko_status()`;
    the user can pause/resume/stop via the matching tools.

The agent does *not* pump targets, mediate measurements, or touch
the RunEngine outside of `ncs_run_plan`. The existing plan owns the
loop.

## Components

### LUCID side

#### `lucid/plugins/agents/autonomous_experiment/`

A subpackage (not a single module) because the tools and prompt body
are each non-trivial. Layout:

```
ncs/src/lucid/plugins/agents/autonomous_experiment/
├── __init__.py         # exports AutonomousExperimentAgent
├── plugin.py           # AgentPlugin subclass
├── nats_tools.py       # 5 @tool functions + one private helper
└── prompts.py          # LUCID-flavored stub prompt
```

`AutonomousExperimentAgent(AgentPlugin)`:

| Attribute | Value |
|---|---|
| `name` | `"autonomous_experiment"` |
| `display_name` | `"Autonomous Experiment"` |
| `description` | `"Design and run GP-driven adaptive experiments via Tsuchinoko"` |
| `category` | `"acquisition"` |
| `priority` | `30` |
| `enabled_by_default` | `True` |
| `get_system_prompt()` | returns `prompts.STUB` |
| `create_tools()` | returns `nats_tools.ALL_TOOLS` |
| `get_references_dir()` | returns `Path(importlib.resources.files("gpcam.skills"))` when `gpcam` is importable, else `None` |

#### Stub prompt (`prompts.STUB`)

Short — only what the agent can't infer from tool docstrings. Pseudo-content:

> **Autonomous experiments.** When the user wants smart/adaptive
> scans, peak finding, or parameter optimisation:
> 1. **Design first** — load gpCAM's `experiment-designer` skill
>    from this plugin's references. If you can't see it, gpCAM
>    isn't installed in LUCID's environment — tell the user
>    `pip install gpcam` and stop. Sibling skills are available:
>    `acquisition-functions`, `kernel-designer`,
>    `prior-mean-functions`, `noise-functions`, `cost-functions`,
>    `gp2scale-advanced`, `multi-task-advanced`.
> 2. **Discover** — `tsuchinoko_discover()`. If empty, tell the
>    user to start Tsuchinoko (`tsuchinoko run`) and stop.
> 3. **Custom callables** — for each user-authored function,
>    `tsuchinoko_upload_design_code(name, kind, code)` and use
>    `"user:<name>"` as the ref in configure.
> 4. **Configure** — `tsuchinoko_configure(payload)`.
> 5. **Run** — `ncs_run_plan(plan_name="adaptive_experiment",
>    params={...})`.
> 6. **Monitor** — point the user at the Visualization Panel
>    (`AdaptiveHeatmapVisualization`, `AdaptiveHyperparameterPlot`
>    will populate live); use `tsuchinoko_status()` for textual
>    progress.
> 7. **Control** — `tsuchinoko_pause / _resume / _stop`.

#### MCP tools (`nats_tools.py`)

All five share a single private helper that pulls the NATS
connection from `lucid.ipc.service.get_ipc_service()`, sends a
request with a 5 s timeout, and raises with a tool-named message on
wire-level errors or timeouts.

| Tool | Signature | NATS subject | Notes |
|---|---|---|---|
| `tsuchinoko_discover` | `() → list[dict]` | broadcast `_tsuchinoko.discover` | 2 s collection window; returns every responder |
| `tsuchinoko_upload_design_code` | `(name: str, kind: Literal["acquisition","kernel","prior_mean","noise"], code: str) → {ref, path}` | `tsuchinoko.experiment.upload_design_code` | |
| `tsuchinoko_configure` | `(payload: dict) → {status}` | `tsuchinoko.experiment.configure` | payload schema documented in tool docstring; the agent assembles it from the designer skill's output |
| `tsuchinoko_status` | `() → {state, iteration, data_count}` | `tsuchinoko.status` | |
| `tsuchinoko_pause`, `tsuchinoko_resume`, `tsuchinoko_stop` | `() → {status, state}` | `tsuchinoko.experiment.{pause,resume,stop}` | three separate names for readable transcripts |

Deliberately not in the plugin (the agent already has these):
`ncs_run_plan` (in `plan_tools.py`), `ncs_get_scan_data` (in
`engine_tools.py`), and the device-selection tools in
`device_tools.py`.

#### Registration

One line in `lucid/plugins/builtin_manifest.py`, mirroring the
existing `BeamlineAlignmentAgent` and `ScanPlanningAgent` entries:

```python
import_path="lucid.plugins.agents.autonomous_experiment:AutonomousExperimentAgent",
```

### Tsuchinoko side

A small MR on `LUCID-refactor`. New design+plan docs under
`docs/design/2026-05-19-phase5-rich-configure.md` and the matching
`docs/plans/`.

#### Extended `experiment.configure` payload

`_handle_configure` is rewritten to validate against a TypedDict and
fail fast on unknown keys. Recognised fields:

| Field | Type | Maps to |
|---|---|---|
| `parameter_bounds` | `list[[float, float]]` | engine `bounds.axis_<i>_{min,max}` (existing) |
| `dimensionality` | `int` (optional, inferred from bounds) | engine sanity-check |
| `kernel` | `"matern_1_2" \| "matern_3_2" \| "matern_5_2" \| "se" \| "periodic" \| "user:<name>"` | engine `kernel` |
| `acquisition_function` | `"variance" \| "ucb" \| "ei" \| "user:<name>"` | engine `acquisition_function` |
| `prior_mean` | `null \| "user:<name>"` | engine `prior_mean` |
| `noise_function` | `null \| "user:<name>"` | engine `noise_function` |
| `noise_variances` | `null \| float \| list[float]` | engine `noise_variances` |
| `initial_points` | `int` (default 10) | engine `initial_points` |
| `training_method` | `"global" \| "local" \| "mcmc" \| "adam" \| "hgdl"` | engine `training_method` |
| `hyperparameters` | `null \| list[float]` | engine initial hyperparameters |
| `x_out` | `null \| list[float]` | fvGP multi-task output indices |

`"user:<name>"` refs resolve at configure time via dynamic import
from the user-designs directory. Missing names yield a typed error
reply (`{"status": "error", "message": "unknown user design
acquisition/<name>"}`) and no engine mutation.

Validation is **strict**: unknown top-level keys return
`{"status": "error", "message": "unknown configure field: <key>"}`.
This fails fast on schema drift between sides; clients (the LUCID
plugin and any future client) must keep payloads honest.

#### New `experiment.upload_design_code`

```jsonc
// request payload
{ "name": "my_ucb", "kind": "acquisition", "code": "<python source>" }

// success reply
{ "status": "ok", "ref": "user:my_ucb",
  "path": "/home/<user>/.tsuchinoko/user_designs/acquisition/my_ucb.py" }
```

Order of operations on the handler:

1. Validate `name` against `^[a-z][a-z0-9_]{0,62}$` (no path
   traversal, no overwriting builtins, no shadowing module names).
2. Validate `kind` against the enum
   `{acquisition, kernel, prior_mean, noise}`.
3. `compile(code, name, "exec")` — fail fast on `SyntaxError`,
   surfaced verbatim in the error message.
4. Execute the compiled code in a fresh namespace; verify the
   expected callable name is bound (e.g. `acquisition_function` for
   `kind="acquisition"`). The expected names per kind are documented
   in the tool docstring and the Tsuchinoko design doc.
5. Write the source to
   `<user_designs_root>/<kind>/<name>.py` only after all checks
   pass.
6. Reply with the resolvable ref string.

The user-designs root is `~/.tsuchinoko/user_designs/` by default,
overridable via `$TSUCHINOKO_USER_DIR`. The handler creates the
directory and its `<kind>/` subdirectory on first use. Tests run
against an explicit `tmp_path`-scoped override.

The same dynamic-import resolver is reused by
`experiment.configure` when it encounters a `"user:<name>"` ref.

## Data flow

See the architecture diagram and the demo-flow section above. The
plan owns the iteration loop; the agent's only steady-state action
is `tsuchinoko_status` on user demand. Tools are individually
stateless and idempotent.

## Error handling

| Failure | Where caught | Behaviour |
|---|---|---|
| LUCID IPC not running | tool wrapper | raise: "LUCID IPC is not running; enable it in Settings → IPC and retry." |
| No Tsuchinoko instance responds (2 s) | `tsuchinoko_discover` | return `[]`; stub-prompt step 2 instructs the agent to halt |
| Multiple instances respond | `tsuchinoko_discover` | return all; agent picks (single-instance is the demo expectation, not a constraint) |
| gpCAM not importable | `get_references_dir` returns `None` | stub prompt instructs the agent to halt and tell the user to `pip install gpcam` |
| Unknown user-design ref in configure | Tsuchinoko, wire | tool raises with the typed error message |
| Upload code with syntax error | Tsuchinoko compile gate | error reply *before* the file is written; tool raises with the SyntaxError text |
| Upload code missing the expected callable | same gate | error reply naming the expected signature (e.g. `acquisition_function(x, gp)`) |
| Tiled credentials unavailable when plan starts | existing `_get_tiled_credentials` | existing plan-side error path; no plugin involvement |
| `bind_run` fails (e.g. Tsuchinoko already bound) | existing handler | existing error reply; plan aborts |
| Plan timeout waiting for targets | existing `adaptive_experiment` | existing behaviour; agent learns via `ncs_get_run_status` |
| NATS request timeout (5 s) on any tool | tool wrapper | raise `TimeoutError` naming the subject |
| Unknown `configure` top-level key | Tsuchinoko strict validation | typed error reply; tool raises with the offending key |

## Security boundary

`upload_design_code` lets the LUCID embedded agent ship Python that
Tsuchinoko will execute in-process. That capability is **not new**:
anything that can post to `tsuchinoko.*` today can already attach
arbitrary callables via `engine.set_parameter` against the
pyqtgraph param tree. The new action makes the existing trust
boundary explicit and auditable (files persist; an operator can
read them).

Authentication and confidentiality for the NATS bus are governed by
the existing IPC design (TLS, no broker credentials, trust prompts
for token sharing). This spec does not alter that posture. Per-
action ACLs over NATS are out of scope and tracked as future work.

## Testing

### Tsuchinoko side

Unit tests (no broker required):

- `test_configure_typed_payload` — each new field maps to the right
  engine knob.
- `test_configure_unknown_key_rejected` — strict validation.
- `test_configure_user_ref_resolves` — `"user:<name>"` imports a
  previously uploaded callable.
- `test_configure_unknown_user_ref` — typed error, engine
  untouched.
- `test_upload_design_code_happy` — file lands at the right path;
  ref string returned.
- `test_upload_design_code_syntax_error` — `SyntaxError` returned
  before any file is written.
- `test_upload_design_code_missing_callable` — wrong/absent function
  name yields the typed error.
- `test_upload_design_code_name_validation` — path traversal
  (`../foo`, `foo/bar`), leading digits, uppercase, builtins —
  rejected.
- `test_user_dir_env_override` — `$TSUCHINOKO_USER_DIR` honoured.

Integration test (requires a NATS broker; opts in via env var):

- `test_configure_roundtrip_nats` — uploads a small custom
  acquisition function, sends a configure payload that references
  it, asserts the engine reflects the change.

Reuses the Phase-2 NATS test fixture.

### LUCID side

Unit tests:

- `test_plugin_registered` — manifest lists `autonomous_experiment`;
  `get_introspection_data()` reports `has_prompt=True`,
  `has_tools=True`.
- `test_references_dir_with_gpcam` — when gpCAM is importable, the
  returned path resolves to the gpCAM skills tree.
- `test_references_dir_without_gpcam` — returns `None`; the stub
  prompt continues to mention the install hint.
- `test_tsuchinoko_discover_no_instance` — no responders within the
  2 s window → returns `[]` (no exception).
- `test_tsuchinoko_discover_one_instance` — one fake responder is
  returned with the expected fields.
- `test_tsuchinoko_configure_passthrough` — payload forwarded
  verbatim; reply unwrapped.
- `test_tsuchinoko_upload_design_code_passthrough` — same.
- `test_pause_resume_stop_passthrough` — three tools hit three
  distinct subjects.
- `test_nats_unavailable` — IPC service absent → tool raises with
  the actionable message.
- `test_nats_timeout` — broker silent → tool raises `TimeoutError`
  naming the subject.

Patterns match the existing IPC tests under `tests/integration/test_ipc_*.py`.

### End-to-end smoke test (manual, scripted)

`ncs/scripts/demo_autonomous_experiment.py`:

1. Optionally start a local NATS server (`$NATS_TEST_AUTOSTART`).
2. Launch `tsuchinoko run --nats nats://localhost:4222` in a
   subprocess.
3. Drive the demo flow via the embedded agent in `--print` mode
   against a synthetic detector + two soft motors.
4. Tail the resulting Tiled run for the `adaptive` stream and
   assert at least three `iter_NNN` containers landed.

Not a CI test. Documented as the verification ritual before
tagging.

## Rollout

Cross-repo but orderable.

1. **Tsuchinoko first.** Extend `configure`, add
   `upload_design_code`, ship unit + integration tests. Strict
   validation means clients must keep up; today's `parameter_bounds`-
   only payload still works (still in the typed schema).
2. **LUCID second.** Feature branch
   `feature/autonomous-experiment-agent` adds the AgentPlugin and
   manifest entry. CI runs unit tests; integration tests opt-in
   via `$NATS_TEST_AUTOSTART`.
3. **Docs.** This spec ships in `ncs/docs/superpowers/specs/`; the
   manual smoke test ships in `ncs/scripts/`; a short pointer note
   from `tsuchinoko/CLAUDE.md` to this spec.
4. **Deploy order.** Tsuchinoko side deployed first (wherever it
   runs). LUCID side merged after. Reverse-order deploys would let
   the LUCID plugin send keys an old Tsuchinoko rejects.

## Out of scope (future specs)

- Per-instance routing (`tsuchinoko.<id>.*`) when multiple
  Tsuchinoko instances coexist on one bus.
- A dedicated Autonomous Experiment dock panel subscribing to
  `tsuchinoko.state` and `tsuchinoko.gp.updated` (the embedded
  agent + adaptive viz widgets cover the demo without it).
- Persistent run history of uploaded designs (today they live in
  the per-user filesystem; a registry that survives upgrades is
  separate work).
- Per-action ACLs over NATS.
- LUCID-managed Tsuchinoko lifecycle (subprocess supervision).

## References

- LUCID IPC design: `docs/superpowers/specs/2026-04-09-ipc-design.md`
- LUCID SDK-native plugins: `docs/superpowers/specs/2026-04-25-lucid-sdk-native-plugins-design.md`
- Tsuchinoko rescope: `tsuchinoko/docs/design/2026-04-12-tsuchinoko-rescope.md`
- Tsuchinoko phase-2 NATS: `tsuchinoko/docs/design/2026-04-12-phase2-nats-integration.md`
- gpCAM skills: `~/PycharmProjects/gpcam/skills/`
- `adaptive_experiment` plan: `ncs/src/lucid/acquire/plans/adaptive.py`
- Adaptive viz widgets: `ncs/src/lucid/visualization/widgets/adaptive/`
