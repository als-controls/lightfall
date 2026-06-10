# Running Plans

Plans are the primary way to perform data acquisition in Lightfall. A plan defines a sequence of measurements - moving motors, triggering detectors, and recording data. This guide explains how to select, configure, and run plans.

## The Bluesky Panel

The Bluesky panel is your main interface for plan execution. Open it from **View** > **Panels** > **Bluesky** if not already visible.

The panel has three main areas:

1. **Plan Selector** (top): Browse and select available plans
2. **Parameter Configuration** (middle): Set up the plan parameters
3. **Run Controls** (toolbar): Start, pause, and abort execution

## Selecting a Plan

### Plan Categories

Plans are organized into categories for easier browsing:

| Category | Description | Examples |
|----------|-------------|----------|
| **Scan** | Move motors and measure | `scan`, `rel_scan` |
| **Grid** | 2D grid measurements | `grid_scan`, `spiral_scan` |
| **Fly** | Continuous motion scans | `fly_scan` |
| **Count** | Fixed-position measurements | `count` |
| **Alignment** | Beam and sample alignment | `tune_centroid` |
| **Calibration** | Detector/motor calibration | Various |

### Finding Plans

To find a specific plan:

1. Use the **search box** to filter by name
2. Or browse by **category** using the dropdown
3. Click a plan to select it

The plan's description appears below the list, explaining what it does and its parameters.

## Configuring Parameters

After selecting a plan, the parameter configuration area shows controls for each parameter.

### Parameter Types

Plans have different types of parameters:

**Devices** (Motors/Detectors):
- Click the dropdown to see available devices
- Only compatible devices are shown (e.g., motor parameters only show motors)
- Multiple detectors can often be selected

**Numeric Values**:
- Enter values directly in the text field
- Some have minimum/maximum limits
- Units are shown where applicable

**Ranges**:
- Start and end values define the scan range
- Step size or number of points controls resolution

### Example: Configuring a 1D Scan

For a basic `scan` plan:

1. **Detectors**: Select one or more detectors to read
2. **Motor**: Choose the motor to move
3. **Start**: Enter the starting position
4. **Stop**: Enter the ending position
5. **Num Points**: Enter how many measurements to take

## Running the Plan

### Starting Execution

Once configured:

1. Click **Run** in the toolbar (play button)
2. The plan is queued for execution
3. Execution begins automatically

During execution:
- The toolbar shows the current state (Running)
- Progress appears in the status area
- Data flows to the Logbook automatically

### Pausing and Resuming

To pause a running plan:

1. Click **Pause** in the toolbar
2. The plan stops at the next safe point
3. Click **Resume** to continue

Pausing is useful for:
- Checking intermediate results
- Addressing unexpected conditions
- Taking a break without losing progress

### Aborting

To stop a plan immediately:

1. Click **Abort** in the toolbar
2. Confirm the abort action
3. The plan stops and cleans up

**Note**: Aborted plans cannot be resumed. Data collected before abort is preserved.

## Monitoring Progress

### Real-Time Feedback

While a plan runs:

- **Logbook**: Shows automatic entries for each step
- **Devices**: Displays current device values
- **Status Bar**: Shows overall progress

### Completion Notification

When a plan finishes:

- A toast notification appears indicating success or failure
- The Logbook records the final status
- The toolbar returns to idle state

## User Plans

You can create custom plans for repetitive procedures.

### Creating a New Plan

1. In the Bluesky panel toolbar, click **New Plan**
2. Your configured code editor (VSCode or PyCharm) opens
3. Write your plan using the Bluesky plan template
4. Save the file

Plans are stored in `~/lightfall/plans/` and automatically loaded.

### Refreshing Plans

After editing a user plan:

1. Click **Refresh Plans** in the toolbar
2. The plan registry reloads
3. Your updated plan appears in the selector

### User Plan Template

A basic user plan:

```python
"""
My Custom Scan

A description of what this plan does.
"""
from bluesky import plans as bp

def my_custom_scan(detectors, motor, start, stop, num):
    """
    Perform a custom scan.

    Parameters
    ----------
    detectors : list
        Detectors to read
    motor : Motor
        Motor to move
    start : float
        Starting position
    stop : float
        Ending position
    num : int
        Number of points
    """
    yield from bp.scan(detectors, motor, start, stop, num)
```

## Troubleshooting

### Plan won't start

- Check that you're authenticated (not in guest mode)
- Verify all required parameters are filled
- Ensure selected devices are connected

### Plan runs but no data appears

- Check detector connections in the Devices panel
- Verify Tiled data catalog is configured (if using)
- Look for errors in the Logging panel

### Plan aborts unexpectedly

- Check the Logbook for error messages
- Review device status for hardware issues
- Check the Logging panel for detailed error information
