---
name: plan-review
description: Review every plan you write before it runs - dry-run preview, limit and move checks, duration, common pitfalls
---

## Plan review

You write almost every plan that runs on this beamline, so you are also its
first reviewer. Before submitting any plan you wrote or modified (with
`lightfall_run_plan_code`, or `lightfall_run_plan` with parameters you chose),
work through this checklist. Skip it only for a plan the user has already run
today with the same parameters.

### 1. Preview it

Call `lightfall_preview_plan` with exactly the code or plan_name/params you
intend to run. It simulates the plan without moving anything and returns the
moves, limits, events, detectors, a duration estimate and warnings.

If the preview reports `error`, fix the plan; do not run it to "see what
happens".

### 2. Read the preview like a reviewer

- **Limits.** Any `violations` or `ok: false` means the plan will fail or
  hit a limit. Fix the targets. Never widen limits to make a plan fit.
- **Moves.** Check each moved device is one the user meant to move, and that
  `target_range` matches what they asked for (units!). A warning about a large
  move from the current position deserves a sentence to the user.
- **Sensitive devices.** Shutters, valves, gate valves, HV or bias supplies:
  confirm with the user before running, every time, even if they asked.
- **End state.** If a motor is left away from where it started, say where it
  will be left. Prefer relative plans (`rel_scan`, `rel_grid_scan`) or
  `bpp.reset_positions_wrapper` when the user expects to come back.
- **Detectors and events.** A run with no detector or no events records
  nothing useful. Check the detector list is the one the user expects.
- **Duration.** `estimated_duration_s` is a lower bound (no readout, settling
  or overhead). Real time is often 1.5-3x. If it is over ~10 minutes, or
  unknown because exposure times could not be read, tell the user the point
  count and per-point exposure and ask before running.
- **Points.** Sanity-check point density: step size vs. the feature width
  you are looking for, and total points vs. the time the user has.

### 3. Pitfalls to check in the code itself

- Every device action goes through messages (`yield from bps.mv(...)`,
  `bps.abs_set`, `bp.scan`...). **Never call device methods directly**
  (`motor.set(1)`, `det.trigger()`) inside plan code: that bypasses the
  RunEngine (and the preview, which would execute it for real).
- Reads (`bps.trigger_and_read`, `bps.read`) only inside a run
  (`@bpp.run_decorator(md=...)` or a `bp.*` plan).
- No reserved metadata keys with the wrong type (`scan_id`, `uid`, `time`).
- `bps.mv` for moves you need finished before continuing; `bps.abs_set`
  without `wait` only when you really want them concurrent.
- Fly scans: `kickoff`/`complete`/`collect` in that order; flyers aren't
  covered by step-scan `per_step` logic.
- Loops: compute the total number of iterations up front and mention it;
  avoid unbounded `while` loops (the preview stops at a message cap and warns).
- Energy or other coupled axes: make sure the plan moves the pseudo-axis the
  user means, not one of its real motors.

### 4. Tell the user, then run

Summarise in two or three lines before submitting: what moves (from where to
where), which detectors, how many points, roughly how long, and anything the
preview warned about. Then submit with a one-sentence `reason` if the run tool
takes one. If anything in step 2 needed a decision from the user, wait for it.

### Rehearsal on a replica

If the session has a Prelude replica or simulated devices available instead of
hardware, and the plan is long, new, or touches several axes, offer to run it
there first. The preview checks what the plan asks for; a rehearsal also shows
what the devices do in response.
