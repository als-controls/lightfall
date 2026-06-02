# Bluesky Plan Design API Reference

This document provides the full API reference for designing Bluesky plans with LUCID UI annotations.

## Required Imports

```python
from __future__ import annotations
from typing import Annotated, Any, Generator
from bluesky import plan_stubs as bps
from bluesky import plans as bp
from lightfall.ui.annotations import (
    Unit, Decimals, Range, Default,
    DeviceFilter, DeviceFilterAny, DeviceDefault,
)

# Type aliases
Device = Any
Motor = Any
Detector = Any
```

## lightfall-ui-annotations

These annotations enable automatic UI generation for plan parameters.

### Unit(suffix: str)
Display unit suffix next to numeric inputs:
```python
energy: Annotated[float, Unit("eV")] = 100.0
exposure: Annotated[float, Unit("s")] = 1.0
temperature: Annotated[float, Unit("K")] = 300.0
field: Annotated[float, Unit("G")] = 0.0
position: Annotated[float, Unit("mm")] = 0.0
```

### Decimals(places: int)
Control float precision in spinbox:
```python
step_size: Annotated[float, Decimals(4)] = 0.001  # 0.0001 precision
position: Annotated[float, Unit("um"), Decimals(7)] = 0.0
```

### Range(min, max)
Set bounds for numeric inputs:
```python
num_points: Annotated[int, Range(1, 1000)] = 10
exposure: Annotated[float, Range(0.001, 3600), Unit("s")] = 1.0
```

### Default(value)
Override the function signature default:
```python
wait_time: Annotated[float, Default(0.5), Unit("s")] = 0.5
temperatures: Annotated[list[float], Default([26.9, 26.8, 26.6])]
```

### DeviceFilter(device_class, category, group, source, name_pattern)
Filter device selection (all criteria use AND logic):
```python
motor: Annotated[Motor, DeviceFilter(device_class="EpicsMotor")]
detector: Annotated[Detector, DeviceFilter(category="detector", group="areadetectors")]
magnet: Annotated[Motor, DeviceFilter(group="magnets")]
```

### DeviceFilterAny(*filters)
Combine filters with OR logic:
```python
axis: Annotated[Device, DeviceFilterAny(
    DeviceFilter(category="motor"),
    DeviceFilter(category="positioner"),
)]
```

### DeviceDefault(*names, pattern=None)
Pre-select devices:
```python
detector: Annotated[list[Detector], DeviceDefault("PI_MTE3")]
motors: Annotated[list[Motor], DeviceDefault(pattern="sample_.*")]
```

### Combining Annotations
Stack multiple annotations for rich UI:
```python
def my_scan(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    energy: Annotated[float, Unit("eV"), Range(100, 2000)],
    step_size: Annotated[float, Unit("um"), Decimals(4), Default(0.1)],
): ...
```

## bluesky-plan-stubs

Plan stubs are the **low-level building blocks** for plans. Master these.

### Movement Stubs

**bps.mv(*args)** - Move devices and wait
```python
yield from bps.mv(motor, 10.0)
yield from bps.mv(motor1, 10.0, motor2, 20.0)  # Multiple simultaneous
```

**bps.mvr(*args)** - Move relative and wait
```python
yield from bps.mvr(motor, 1.0)  # Move 1.0 from current position
```

**bps.abs_set(obj, value, group=None, wait=False)** - Set absolute position
```python
yield from bps.abs_set(motor, 10.0, wait=True)
```

**bps.rel_set(obj, value, group=None, wait=False)** - Set relative position
```python
yield from bps.rel_set(motor, 1.0, wait=True)
```

### Timing/Synchronization Stubs

**bps.sleep(time)** - Wait for specified seconds
```python
yield from bps.sleep(0.5)
```

**bps.wait(group=None)** - Wait for a group to complete
```python
yield from bps.abs_set(motor, 10.0, group="move")
yield from bps.wait(group="move")
```

**bps.checkpoint()** - Mark a resumable point
```python
yield from bps.checkpoint()  # Plan can resume from here on interrupt
```

### Reading/Triggering Stubs

**bps.trigger(obj, group=None, wait=False)** - Trigger acquisition
```python
yield from bps.trigger(detector, wait=True)
```

**bps.read(obj)** - Read device values
```python
yield from bps.read(detector)
```

**bps.trigger_and_read(devices, name="primary")** - Trigger and read in one step
```python
yield from bps.trigger_and_read([detector, motor])
yield from bps.trigger_and_read([detector], name="dark")  # Named stream
```

**bps.one_shot(detectors)** - Single acquisition
```python
yield from bps.one_shot([detector])
```

**bps.one_nd_step(detectors, step, pos_cache, take_reading=None)** - One step in N-D scan
```python
# Used in per_step callbacks
yield from bps.one_nd_step(detectors, step, pos_cache)
```

### Metadata Stubs

**bps.open_run(md=None)** / **bps.close_run(exit_status=None)** - Run boundaries
```python
yield from bps.open_run(md={"sample": "test"})
# ... do acquisition ...
yield from bps.close_run()
```

**bps.create(name="primary")** / **bps.save()** - Event boundaries
```python
yield from bps.create()
yield from bps.read(detector)
yield from bps.save()
```

### Device State Stubs

**bps.stage(obj)** / **bps.unstage(obj)** - Prepare/cleanup device
```python
yield from bps.stage(detector)
# ... use detector ...
yield from bps.unstage(detector)
```

**bps.configure(obj, config_dict)** - Configure device
```python
yield from bps.configure(detector, {"exposure_time": 1.0})
```

### Utility Stubs

**bps.null()** - No-op (useful for conditional logic)
```python
yield from bps.null()
```

**bps.repeat(plan, num=1, delay=0)** - Repeat a plan
```python
yield from bps.repeat(my_sub_plan, num=5, delay=1.0)
```

**bps.rd(obj)** - Read single value (returns value, not generator!)
```python
current_pos = yield from bps.rd(motor)  # Returns the value
```

## bluesky-plans

High-level plans built from stubs. Use these for common patterns.

### Linear Scans

**bp.scan(detectors, motor, start, stop, num)** - Absolute linear scan
```python
yield from bp.scan([detector], motor, 0, 10, 11)
```

**bp.rel_scan(detectors, motor, start, stop, num)** - Relative linear scan
```python
yield from bp.rel_scan([detector], motor, -5, 5, 11)
```

### List Scans

**bp.list_scan(detectors, motor, positions)** - Scan through list
```python
yield from bp.list_scan([detector], motor, [1, 2, 5, 10])
```

**bp.rel_list_scan(detectors, motor, positions)** - Relative list scan
```python
yield from bp.rel_list_scan([detector], motor, [-1, 0, 1])
```

### Grid Scans

**bp.grid_scan(detectors, motor1, s1, e1, n1, motor2, s2, e2, n2, ...)** - N-D grid
```python
yield from bp.grid_scan(
    [detector],
    motor1, 0, 10, 11,  # axis 1: start, stop, num
    motor2, 0, 5, 6,    # axis 2: start, stop, num
)
```

**bp.rel_grid_scan(...)** - Relative grid scan
```python
yield from bp.rel_grid_scan([detector], motor1, -5, 5, 11, motor2, -2, 2, 5)
```

### Spiral Scans

**bp.spiral(detectors, x_motor, y_motor, x_start, y_start, x_range, y_range, dr, nth)**
**bp.spiral_fermat(detectors, x_motor, y_motor, x_start, y_start, x_range, y_range, dr, factor)**
**bp.spiral_square(detectors, x_motor, y_motor, x_center, y_center, x_range, y_range, x_num, y_num)**

### Adaptive Scans

**bp.adaptive_scan(detectors, target_field, motor, start, stop, min_step, max_step, target_delta, ...)**
```python
yield from bp.adaptive_scan([detector], "det_value", motor, 0, 10, 0.01, 1.0, 0.1)
```

### Count

**bp.count(detectors, num=1, delay=0)** - Fixed-position acquisition
```python
yield from bp.count([detector], num=10, delay=1.0)
```

### Common Parameters in bp.* Plans

- **per_step**: Custom step function `def per_step(detectors, step, pos_cache)`
- **md**: Metadata dictionary to add to run
- **snake_axes**: For grids, alternate direction (snaking)

## plan-design-patterns

### Custom per_step for Delays
```python
def my_plan(detectors, motor, start, stop, num, wait_time=0.5):
    def wait_per_step(detectors, step, pos_cache):
        yield from bps.sleep(wait_time)
        yield from bps.one_nd_step(detectors, step, pos_cache)

    yield from bp.scan(detectors, motor, start, stop, num, per_step=wait_per_step)
```

### Custom Trigger for Dark Collection
```python
def my_plan(detectors, motor, start, stop, num, collect_darks=False):
    def custom_trigger(dets, name="primary"):
        yield from bps.trigger_and_read(dets, name=name)
        if collect_darks:
            yield from bps.trigger_and_read(dets, name="dark")

    def custom_step(detectors, step, pos_cache):
        yield from bps.one_nd_step(detectors, step, pos_cache, take_reading=custom_trigger)

    yield from bp.scan(detectors, motor, start, stop, num, per_step=custom_step)
```

### Sequential Motor Movement with Delays
```python
from bluesky import utils as bsu

def sequential_step(detectors, step, pos_cache, delays):
    yield from bps.checkpoint()
    grp = bps._short_uid("set")

    for (motor, pos), delay in zip(step.items(), delays):
        if pos != pos_cache.get(motor):
            yield bsu.Msg("set", motor, pos, group=grp)
            yield bsu.Msg("wait", None, group=grp)
            yield from bps.sleep(delay)
            pos_cache[motor] = pos

    yield from bps.trigger_and_read(list(detectors) + list(step.keys()))
```

### Nested Scans
```python
def nested_scan(detectors, outer_motor, outer_vals, inner_motor, inner_start, inner_stop, inner_num):
    for val in outer_vals:
        yield from bps.mv(outer_motor, val)
        yield from bps.sleep(1.0)  # Stabilization
        yield from bp.scan(detectors, inner_motor, inner_start, inner_stop, inner_num)
```

## plan-signature-best-practices

1. **Always include type hints** with Annotated for UI generation
2. **Order parameters**: detectors first, then motors, then scan parameters
3. **Use descriptive names**: energy_min/energy_max not e1/e2
4. **Provide sensible defaults** for optional parameters
5. **Write clear docstrings** with Args section
6. **Return type**: `Generator[Any, Any, Any]`

## complete-example

```python
def energy_scan_with_wait(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    energy_motor: Annotated[
        Motor,
        DeviceDefault("mono_energy"),
    ],
    energy_min: Annotated[float, Unit("eV"), Range(100, 2000)],
    energy_max: Annotated[float, Unit("eV"), Range(100, 2000)],
    num_points: Annotated[int, Range(1, 500)],
    wait_time: Annotated[float, Unit("s"), Default(1.0), Range(0, 60)] = 1.0,
    collect_darks: bool = False,
) -> Generator[Any, Any, Any]:
    """Energy scan with configurable wait time at each point.

    Scans through energy values with optional dark frame collection.

    Args:
        detectors: Detectors to read at each point.
        energy_motor: Monochromator energy motor.
        energy_min: Starting energy in eV.
        energy_max: Ending energy in eV.
        num_points: Number of energy points.
        wait_time: Time to wait after energy move before reading.
        collect_darks: Whether to collect dark frames.

    Yields:
        Bluesky plan messages.
    """
    def custom_trigger(dets, name="primary"):
        yield from bps.sleep(wait_time)
        yield from bps.trigger_and_read(dets, name=name)
        if collect_darks:
            yield from bps.trigger_and_read(dets, name="dark")

    def custom_step(detectors, step, pos_cache):
        yield from bps.one_nd_step(detectors, step, pos_cache, take_reading=custom_trigger)

    yield from bp.scan(
        detectors,
        energy_motor, energy_min, energy_max, num_points,
        per_step=custom_step,
    )
```
