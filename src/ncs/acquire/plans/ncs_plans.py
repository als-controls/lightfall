"""Custom NCS plans that wrap standard Bluesky plans.

These plans provide simplified interfaces for common use cases,
hiding complexity from users who don't need the full flexibility
of the underlying Bluesky plans.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generator

if TYPE_CHECKING:
    pass


def scan_1d(
    detectors: list,
    motor: Any,
    start: float,
    stop: float,
    num: int,
) -> Generator[Any, Any, Any]:
    """Simple 1D scan over a single motor.

    A simplified interface to the standard Bluesky scan() plan,
    restricted to a single motor dimension. Use this for basic
    step scans along one axis.

    Args:
        detectors: List of detectors to read at each point.
        motor: Motor to scan.
        start: Starting position.
        stop: Ending position.
        num: Number of points.

    Yields:
        Bluesky plan messages.

    Example:
        >>> RE(scan_1d([det], motor, -10, 10, 21))
    """
    from bluesky import plans as bp

    yield from bp.scan(detectors, motor, start, stop, num)


def rel_scan_1d(
    detectors: list,
    motor: Any,
    start: float,
    stop: float,
    num: int,
) -> Generator[Any, Any, Any]:
    """Relative 1D scan over a single motor.

    Like scan_1d, but start and stop are relative to the current
    motor position.

    Args:
        detectors: List of detectors to read at each point.
        motor: Motor to scan (start/stop relative to current position).
        start: Starting offset from current position.
        stop: Ending offset from current position.
        num: Number of points.

    Yields:
        Bluesky plan messages.

    Example:
        >>> RE(rel_scan_1d([det], motor, -5, 5, 11))
    """
    from bluesky import plans as bp

    yield from bp.rel_scan(detectors, motor, start, stop, num)


def register_ncs_plans(registry) -> None:
    """Register custom NCS plans in the given registry.

    Args:
        registry: PlanRegistry to register plans in.
    """
    from ncs.acquire.plans.registry import PlanInfo

    # 1D Scan - simple version of scan
    plan_info = PlanInfo.from_function(
        name="scan_1d",
        func=scan_1d,
        category="scan",
    )
    plan_info.display_name = "1D Scan"
    plan_info.icon = ("#4CAF50", "1")  # Green with "1"
    registry._plans[plan_info.name] = plan_info
    registry._categories.add(plan_info.category)

    # Relative 1D Scan
    plan_info = PlanInfo.from_function(
        name="rel_scan_1d",
        func=rel_scan_1d,
        category="scan",
    )
    plan_info.display_name = "Relative 1D Scan"
    plan_info.icon = ("#4CAF50", "R")  # Green with "R"
    registry._plans[plan_info.name] = plan_info
    registry._categories.add(plan_info.category)
