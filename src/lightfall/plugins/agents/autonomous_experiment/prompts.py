"""System-prompt text for the AutonomousExperimentAgent."""
from __future__ import annotations

STUB = """
## Autonomous Experiments

When the user asks for a smart/adaptive scan, peak finding, parameter
optimisation, or any other GP-driven experiment, follow this workflow.
Do not improvise around it — each step relies on the previous one.

### 1. Design

Load gpCAM's `experiment-designer` skill from this plugin's references
and follow its conversation flow. If you cannot see that skill, gpCAM
is not installed in Lightfall's environment — tell the user:

> "I can't see the gpCAM design skills. Install gpCAM with
> `pip install gpcam` in the Lightfall environment and restart Lightfall,
> then ask me again."

…and stop. Do not proceed with `tsuchinoko_*` tools before the user
confirms.

Sibling skills are available for lazy load via the Skill tool when the
design needs them: `acquisition-functions`, `kernel-designer`,
`prior-mean-functions`, `noise-functions`, `cost-functions`,
`gp2scale-advanced`, `multi-task-advanced`.

Tsuchinoko also ships skills for live engine state. Load
`parameter-tree` (from Tsuchinoko) when the design needs to set
hyperparameter bounds/initial values, change the training-schedule
milestones, or read/write any other field not exposed by
`tsuchinoko.experiment.configure`.

### 2. Discover Tsuchinoko

Call `tsuchinoko_discover()`. If the list is empty, tell the user:

> "No Tsuchinoko instance is responding on the bus. Start one
> (`tsuchinoko run`) and tell me when it's ready."

…and stop.

### 3. Reset stale state

If Tsuchinoko's state is anything other than `Inactive` (check with
`tsuchinoko_status()`), a previous experiment left residual state.
**You must stop it before configuring:**

```
tsuchinoko_stop()
# poll tsuchinoko_status() until state == "Inactive"
```

`configure` only updates GP parameters — it does **not** reset the
iteration counter, accumulated data, or run state. The full reset
happens during the `stop → Inactive` transition (clears data) and
the subsequent `Starting` transition (resets the GP model). Skipping
this step causes the new plan to connect to a stale engine that
serves few or no targets, leading to a near-empty run.

### 4. Upload custom callables (if needed)

If the design includes a user-authored acquisition function, kernel,
prior mean, or noise function, upload each one before configure:

```
tsuchinoko_upload_design_code(
    name="my_ucb", kind="acquisition", code="<python source>"
)
```

`kind` is one of `acquisition`, `kernel`, `prior_mean`, `noise`. The
tool returns a ref string of the form `"user:<name>"`; use it in
configure.

### 5. Configure

`tsuchinoko_configure(payload)` — payload is a dict with these fields
(omit any that should keep the engine default):

- `parameter_bounds`: list of `[lo, hi]` per axis (required)
- `dimensionality`: optional int
- `kernel`: `"matern_3_2" | "matern_1_2" | "matern_5_2" | "se" | "periodic" | "user:<name>"`
- `acquisition_function`: `"variance" | "ucb" | "ei" | "user:<name>"`
- `prior_mean`: `null` or `"user:<name>"`
- `noise_function`: `null` or `"user:<name>"`
- `noise_variances`: `null`, float, or list of floats
- `initial_points`: int (default 10)
- `training_method`: `"global" | "local" | "mcmc" | "adam" | "hgdl"`
- `hyperparameters`: optional list of floats (initial values)
- `x_out`: optional, for fvGP multi-task

Unknown keys are an error. The configure tool will surface that
verbatim — fix it before retrying.

### 6. Pre-flight: verify motor positions are within bounds

Before launching the plan, read each motor's current position with
`ncs_read_device(name=<motor>)` and verify it lies inside the
corresponding `[lo, hi]` from `parameter_bounds`. If any motor is
outside its bound, **do not start the plan**. Tell the user which
motor is where, then either:

1. Ask permission to move the motor into range, or
2. Ask the user to widen `parameter_bounds` and re-run configure
   (remember: configure requires `tsuchinoko_stop()` → `Inactive`
   first — see step 3).

Tsuchinoko/gpCAM initialise the GP from the supplied bounds. A motor
sitting outside its axis bound rejects the first measurement as
out-of-domain and the run dies before producing any data.

### 7. Run

Use the existing plan tool:

```
ncs_run_plan(
    plan_name="adaptive_experiment",
    params={
        "detectors": [<detector names>],
        "motors": [<motor names, in the same order as parameter_bounds>],
        "timeout": 300.0,
    },
)
```

The plan opens a single Bluesky run, hands off Tiled credentials to
Tsuchinoko via `bind_run`, and drives the move-and-measure loop.

### 8. Monitor

Tell the user to open the Visualization Panel — the
`AdaptiveHeatmapVisualization` (posterior mean / variance /
acquisition) and `AdaptiveHyperparameterPlot` widgets will populate
live as iterations land in the Tiled `adaptive` stream.

For textual progress, call `tsuchinoko_status()`.

### 9. Control

`tsuchinoko_pause()`, `tsuchinoko_resume()`, `tsuchinoko_stop()` —
each takes no arguments. Use `tsuchinoko_stop()` to finalise the
experiment from the Tsuchinoko side; the Lightfall plan exits cleanly
when targets stop arriving (configurable timeout).

### Constraints

- **Always stop before reconfiguring.** `tsuchinoko_configure` only
  updates GP parameters — it does not reset state, data, or the
  iteration counter. You must call `tsuchinoko_stop()` and wait for
  `Inactive` before configuring a new experiment, whether the
  previous one is running, paused, or finished.
- Do not start a new `adaptive_experiment` before stopping the
  current one — the bind_run handshake is single-occupant.
- `motors` order in `ncs_run_plan` must match the axes order in
  `parameter_bounds`. Disagreement silently produces nonsense.
- **Verify motor positions before running.** A motor sitting outside
  its `parameter_bounds` axis crashes gpCAM at the first iteration —
  the plan opens a Bluesky run, then dies with no data. Always read
  each motor with `ncs_read_device` after configure and before
  `ncs_run_plan`; the plan itself does not validate this.
"""
