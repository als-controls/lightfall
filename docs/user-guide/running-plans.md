# Running Plans

Plans are how data acquisition happens in Lightfall. A plan is a Bluesky
procedure — move motors, trigger detectors, record data — executed by the
RunEngine. This page covers selecting and configuring plans, controlling
execution, and writing your own.

## The Bluesky panel

Open the **Bluesky** panel from the left sidebar (or **View → Panels →
Acquisition → Bluesky**). It is organized as tabs:

- **Plans** — browse the registered plans, with a search box and category
  filter. Selecting a plan shows its description.
- **Config: \<plan\>** — opens when you select a plan; holds the parameter
  form and the **Run**, **Reset**, and **Edit** buttons.

> 🖼️ **Image placeholder** — *Screenshot: Bluesky panel Plans tab with the plan list and search box*

## Built-in plans

These wrap the standard Bluesky plans with typed parameters, so the panel can
generate a proper form for each:

| Plan | Category | What it does |
|------|----------|--------------|
| **Count** (`count`) | Count | Read detectors *num* times, with optional delay |
| **1D Scan** (`scan_1d`) | Scan | Step one motor from start to stop in *num* points |
| **Relative 1D Scan** (`rel_scan_1d`) | Scan | Same, relative to the current position |
| **2D Grid Scan** (`scan_2d`) | Scan | Two-motor grid, optional snaked inner axis |
| **Relative 2D Grid Scan** (`rel_scan_2d`) | Scan | Same, relative to current positions |
| **List Scan** (`list_scan_1d`) | Scan | Visit an explicit list of positions |
| **Relative List Scan** (`rel_list_scan_1d`) | Scan | Offsets from the current position |
| **Adaptive Scan** (`adaptive_scan`) | Scan | Step size adapts to how fast the signal changes |
| **Tune Centroid** (`tune_centroid`) | Alignment | Iteratively center a motor on a signal peak |
| **Tune Centroid 2D** (`tune_centroid_2d`) | Alignment | Alternate two motors onto a 2D peak |
| **Simple Acquire** (`simple_acquire`) | Acquire | Area-detector acquisition, optional dark frame |
| `adaptive_experiment` | Scan | gpCAM-driven adaptive experiment via Tsuchinoko (when available) |

Beamline deployments and user plans add to this list.

## Configuring parameters

The Config tab renders one input per parameter:

- **Devices** (motors, detectors): dropdowns listing only compatible devices
  from the catalog — motor parameters offer motors, detector parameters offer
  detectors. Multi-detector parameters allow selecting several.
- **Numbers**: text fields, with units and minimum/maximum limits shown where
  the plan declares them.
- **Booleans / choices**: checkboxes and dropdowns.

**Reset** returns all parameters to their defaults. **Edit** opens the plan's
source file in your configured editor (see
[Preferences → External Tools](preferences.md)).

### Example: a 1D scan

1. **detectors** — select one or more detectors to read
2. **motor** — the motor to step
3. **start** / **stop** — the scan range
4. **num** — number of points (default 21)

> 🖼️ **Image placeholder** — *Screenshot: Config tab for 1D Scan with detectors, motor, start/stop/num filled in*

## Running

Click **Run**. A **Sample Metadata** dialog appears first, where you can
enter a sample name and any additional key-value metadata to record with the
run; confirm it to submit the plan to the RunEngine. Submitted plans are
queued — if the engine is busy, the plan waits its turn (see the **Queue**
panel for pending plans and history).

### Engine controls

The **RunEngine control** in the main toolbar shows the engine state and is
where you intervene in a running plan:

- **Pause** — stop at the next checkpoint. The button becomes **Resume**.
- **Stop** — end the run gracefully; data collected so far is kept and the
  run is marked complete.
- **Abort** — end the run immediately; the run is marked aborted.

> 🖼️ **Image placeholder** — *Screenshot: RunEngine control during a paused run, showing Resume, Stop, and Abort*

### Monitoring

- The **Logbook** records each run automatically — plan name, a short run ID,
  and on completion the exit status and event counts.
- The **Documents** panel streams the raw Bluesky documents if you want to
  watch the firehose.
- A **toast notification** reports success or failure when the run ends.
- To plot the data, open the run from the **Data Browser** (double-click) —
  runs still in progress refresh live. See
  [Your First Session](first-session.md).

## User plans

You can add your own plans without touching the Lightfall source. User plans
are Python files in `~/lightfall/plans/` — one file per plan, and the
filename (without `.py`) becomes the plan name.

### Creating a plan

1. In the Bluesky panel title bar, click **New Plan**.
2. Name it; Lightfall creates the file from a template and opens it in your
   configured editor (VSCode or PyCharm — set under **File → Settings →
   External Tools**).
3. Edit and save.

Each file must define a callable named `plan` — a generator function the
RunEngine can execute. The generated template looks like:

```python
"""my_scan - Custom Bluesky plan."""
from __future__ import annotations

from typing import Any, Generator

import bluesky.plans as bp


def plan(
    detectors: list,
    motor: Any,
    start: float = -10.0,
    stop: float = 10.0,
    num: int = 21,
) -> Generator[Any, Any, Any]:
    """Scan motor while reading detectors."""
    yield from bp.scan(detectors, motor, start, stop, num)
```

Type hints matter: the panel builds the parameter form from the signature,
so annotated parameters get proper device dropdowns and numeric fields.

### Reloading

After editing a plan file, click **Refresh** in the Bluesky panel title bar
to reload user plans from disk. The third title-bar button opens the plans
folder in your file manager.

Changes to files in `~/lightfall/plans/` are committed to the local git
repository in `~/lightfall/`, so plan history is preserved — the same
mechanism that tracks agent-built plugins (see
[Customizing Lightfall with the Agent](agent-customization.md)).

```{tip}
You can also ask the Claude assistant to write a user plan for you — describe
the scan and it creates the file via the same `~/lightfall/plans/` mechanism.
```

## Troubleshooting

### Plan won't start

- Guests cannot run plans — check you are logged in with a real account.
- Check all required parameters are filled and the selected devices are
  connected (Devices panel).

### Plan runs but no data appears in the Data Browser

- The Data Browser requires a Tiled server (**File → Settings → Tiled Data
  Catalog**) and shows its connection status at the bottom of the panel.
- Check the **Logging** panel for write errors.

### Plan fails immediately

- Check the Logbook fragment for the exit status, and the **Logging** panel
  for the underlying exception.
- For motor scans: a target position outside the motor's limits is the most
  common cause.
