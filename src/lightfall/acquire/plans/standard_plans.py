"""Standard typed wrapper plans for common Bluesky operations.

These replace the raw bluesky builtins (bp.scan, bp.count, etc.) which use
*args signatures that can't generate useful procedural UIs. Each wrapper
provides proper type hints and LUCID annotations for automatic UI generation.

The raw bp.* plans remain accessible via ncs_run_plan_code and the IPython
console for power users who need the full flexibility.

Usage:
    from lightfall.acquire.plans.standard_plans import register_standard_plans
    register_standard_plans(registry)
"""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING, Annotated, Any

from bluesky import plans as bp

from lightfall.ui.annotations import Default, DeviceDefault, DeviceFilter, Unit

if TYPE_CHECKING:
    from lightfall.acquire.plans.registry import PlanRegistry

# Type aliases
Device = Any
Motor = Any
Detector = Any


# =============================================================================
# Count
# =============================================================================


def count(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    num: Annotated[int, Default(1)] = 1,
    delay: Annotated[float | None, Unit("s"), Default(None)] = None,
) -> Generator[Any, Any, Any]:
    """Count detectors one or more times.

    Takes one or more readings from the specified detectors without
    moving any motors.

    Args:
        detectors: Detectors to read.
        num: Number of readings to take.
        delay: Delay between readings in seconds (None for no delay).

    Yields:
        Bluesky plan messages.
    """
    yield from bp.count(detectors, num=num, delay=delay)


# =============================================================================
# Relative 1D Scan
# =============================================================================


def rel_scan_1d(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    motor: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
    ],
    start: float,
    stop: float,
    num_points: int,
) -> Generator[Any, Any, Any]:
    """Relative 1D scan over a single motor.

    Scans the motor relative to its current position. Start and stop
    are offsets from the current position.

    Args:
        detectors: Detectors to read at each point.
        motor: Motor to scan.
        start: Starting offset from current position.
        stop: Ending offset from current position.
        num_points: Number of points in the scan.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.rel_scan(detectors, motor, start, stop, num_points)


# =============================================================================
# Adaptive Scan
# =============================================================================


def adaptive_scan_1d(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    target_field: str,
    motor: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
    ],
    start: float,
    stop: float,
    min_step: Annotated[float, Default(0.01)] = 0.01,
    max_step: Annotated[float, Default(5.0)] = 5.0,
    target_delta: Annotated[float, Default(0.1)] = 0.1,
    backstep: Annotated[bool, Default(True)] = True,
    threshold: Annotated[float, Default(0.8)] = 0.8,
) -> Generator[Any, Any, Any]:
    """Adaptive 1D scan that adjusts step size based on signal change.

    Automatically uses smaller steps where the signal is changing rapidly
    and larger steps where it is relatively flat.

    Args:
        detectors: Detectors to read at each point.
        target_field: Name of the data field to use for adaptive logic.
        motor: Motor to scan.
        start: Starting position.
        stop: Ending position.
        min_step: Minimum step size.
        max_step: Maximum step size.
        target_delta: Target change in signal between points.
        backstep: Whether to allow backstepping.
        threshold: Threshold for step size adjustment.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.adaptive_scan(
        detectors,
        target_field,
        motor,
        start,
        stop,
        min_step,
        max_step,
        target_delta,
        backstep,
        threshold,
    )


# =============================================================================
# Registration
# =============================================================================


def register_standard_plans(registry: PlanRegistry) -> None:
    """Register standard typed wrapper plans.

    Args:
        registry: PlanRegistry to register plans in.
    """
    from lightfall.acquire.plans.registry import PlanInfo

    plans_to_register = [
        ("count", count, "count", "Count", ("#2196F3", "C")),
        ("rel_scan_1d", rel_scan_1d, "scan", "Relative 1D Scan", ("#4CAF50", "R")),
        ("adaptive_scan_1d", adaptive_scan_1d, "scan", "Adaptive Scan", ("#FF9800", "A")),
    ]

    for name, func, category, display_name, icon in plans_to_register:
        plan_info = PlanInfo.from_function(
            name=name,
            func=func,
            category=category,
        )
        plan_info.display_name = display_name
        plan_info.icon = icon
        registry._plans[plan_info.name] = plan_info
        registry._categories.add(plan_info.category)
