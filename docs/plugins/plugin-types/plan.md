# PlanPlugin

Plan plugins register Bluesky plans for data acquisition.

## Purpose

Use `PlanPlugin` when you want to:
- Add custom Bluesky scan plans
- Provide beamline-specific measurement procedures
- Create reusable acquisition workflows

## Base Class

```python
from lucid.plugins.plan_plugin import PlanPlugin
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"plan"` | Plugin type identifier |
| `is_singleton` | `True` | One instance per plugin |

## Required Methods

### name (property)

Unique identifier for this plan.

```python
@property
def name(self) -> str:
    return "my_scan"
```

### get_plan_function()

Return the Bluesky plan generator function.

```python
def get_plan_function(self) -> Callable[..., Generator[Any, Any, Any]]:
    """Return the plan generator function.

    Returns:
        A Bluesky plan generator function.
    """
    return self._my_scan

def _my_scan(self, detectors, motor, start, stop, num):
    """My custom scan plan."""
    import bluesky.plans as bp
    yield from bp.scan(detectors, motor, start, stop, num)
```

## Optional Methods

### category (property)

Category for grouping plans in the UI. Default: `"general"`.

```python
@property
def category(self) -> str:
    return "alignment"  # or "scan", "calibration", etc.
```

### plan_description (property)

Description for UI display. Defaults to the plan function's docstring.

```python
@property
def plan_description(self) -> str:
    return "Perform a custom scan with optimized parameters."
```

### get_plan_info()

Get `PlanInfo` for registration. Usually not overridden.

```python
def get_plan_info(self) -> PlanInfo:
    from lucid.acquire.plans.registry import PlanInfo
    return PlanInfo.from_function(
        name=self.name,
        func=self.get_plan_function(),
        category=self.category,
    )
```

## Lifecycle

1. Plugin is instantiated on load
2. `get_plan_info()` creates `PlanInfo` metadata
3. Plan is registered with `PlanRegistry`
4. Plan appears in the acquisition UI
5. User can configure and execute the plan

## Bluesky Plan Basics

Bluesky plans are Python generators that yield messages to the RunEngine:

```python
import bluesky.plans as bp
import bluesky.plan_stubs as bps

def my_plan(motor, start, stop, num):
    """A simple scan plan.

    Args:
        motor: Motor to scan.
        start: Start position.
        stop: Stop position.
        num: Number of points.
    """
    # Move to start
    yield from bps.mv(motor, start)

    # Perform scan
    yield from bp.scan([], motor, start, stop, num)
```

## Complete Example

```python
"""Grid scan plan plugin for 2D mapping."""

from typing import Any, Callable, Generator

from lucid.plugins.plan_plugin import PlanPlugin


class GridScanPlan(PlanPlugin):
    """Plan plugin for 2D grid scans."""

    @property
    def name(self) -> str:
        return "grid_scan_2d"

    @property
    def category(self) -> str:
        return "scan"

    @property
    def plan_description(self) -> str:
        return "Perform a 2D grid scan over two motors."

    def get_plan_function(self) -> Callable[..., Generator[Any, Any, Any]]:
        return self._grid_scan_2d

    def _grid_scan_2d(
        self,
        detectors: list,
        motor1,
        start1: float,
        stop1: float,
        num1: int,
        motor2,
        start2: float,
        stop2: float,
        num2: int,
        snake: bool = False,
    ):
        """Perform a 2D grid scan.

        Scans motor1 in the outer loop and motor2 in the inner loop,
        reading detectors at each point.

        Args:
            detectors: List of detectors to read.
            motor1: Outer loop motor.
            start1: Start position for motor1.
            stop1: Stop position for motor1.
            num1: Number of points for motor1.
            motor2: Inner loop motor.
            start2: Start position for motor2.
            stop2: Stop position for motor2.
            num2: Number of points for motor2.
            snake: If True, alternate motor2 direction on each row.
        """
        import bluesky.plans as bp

        yield from bp.grid_scan(
            detectors,
            motor1, start1, stop1, num1,
            motor2, start2, stop2, num2,
            snake_axes=snake,
        )
```

## Plan with Pre/Post Actions

```python
"""Alignment plan with pre and post actions."""

import bluesky.plan_stubs as bps

from lucid.plugins.plan_plugin import PlanPlugin


class AlignmentPlan(PlanPlugin):
    """Plan that performs alignment with setup and teardown."""

    @property
    def name(self) -> str:
        return "alignment_with_setup"

    @property
    def category(self) -> str:
        return "alignment"

    def get_plan_function(self):
        return self._alignment_plan

    def _alignment_plan(self, motor, detector, center, width, num_points):
        """Perform alignment scan with setup and teardown.

        Args:
            motor: Motor to scan.
            detector: Detector to read.
            center: Center position.
            width: Scan width.
            num_points: Number of points.
        """
        import bluesky.plans as bp

        # Pre-scan setup
        yield from bps.mv(motor, center - width / 2)

        # Open shutter (example)
        # yield from bps.mv(shutter, "open")

        try:
            # Perform the scan
            start = center - width / 2
            stop = center + width / 2
            yield from bp.scan([detector], motor, start, stop, num_points)

        finally:
            # Post-scan cleanup
            yield from bps.mv(motor, center)
            # yield from bps.mv(shutter, "closed")
```

## Plan with Adaptive Logic

```python
"""Adaptive scan that adjusts based on results."""

from lucid.plugins.plan_plugin import PlanPlugin


class AdaptiveScanPlan(PlanPlugin):
    """Plan that adapts scan parameters based on results."""

    @property
    def name(self) -> str:
        return "adaptive_scan"

    @property
    def category(self) -> str:
        return "scan"

    def get_plan_function(self):
        return self._adaptive_scan

    def _adaptive_scan(self, motor, detector, start, stop, initial_step):
        """Scan that refines step size based on signal gradient.

        Args:
            motor: Motor to scan.
            detector: Detector to read.
            start: Start position.
            stop: Stop position.
            initial_step: Initial step size.
        """
        import bluesky.plans as bp
        import bluesky.plan_stubs as bps
        import numpy as np

        position = start
        last_value = None
        step = initial_step

        while position < stop:
            # Move and read
            yield from bps.mv(motor, position)
            ret = yield from bps.trigger_and_read([detector])

            current_value = ret[detector.name]["value"]

            if last_value is not None:
                gradient = abs(current_value - last_value) / step

                # Adjust step size based on gradient
                if gradient > 0.1:
                    step = max(step / 2, initial_step / 10)
                elif gradient < 0.01:
                    step = min(step * 2, initial_step * 2)

            last_value = current_value
            position += step
```

## Registration

### Built-in Manifest

```python
PluginEntry(
    type_name="plan",
    name="grid_scan_2d",
    import_path="my_package.plans:GridScanPlan",
),
```

### External Package

```python
# my_beamline/manifest.py
manifest = PluginManifest(
    name="beamline-plans",
    plugins=[
        PluginEntry("plan", "bl_grid_scan", "my_beamline.plans:GridScanPlan"),
        PluginEntry("plan", "bl_alignment", "my_beamline.plans:AlignmentPlan"),
    ],
)
```

## Plan Categories

Common categories for organizing plans:

| Category | Purpose |
|----------|---------|
| `"scan"` | General scanning plans |
| `"alignment"` | Beam/sample alignment |
| `"calibration"` | Instrument calibration |
| `"measurement"` | Standard measurements |
| `"custom"` | User-defined plans |
| `"general"` | Default category |

## Parameter Documentation

Document plan parameters in the docstring for UI generation:

```python
def _my_scan(self, motor, start: float, stop: float, num: int = 10):
    """My scan plan.

    Args:
        motor: The motor to scan (ophyd device).
        start: Start position in motor units.
        stop: Stop position in motor units.
        num: Number of points (default: 10).
    """
    ...
```

The docstring is parsed to generate the plan configuration UI.
