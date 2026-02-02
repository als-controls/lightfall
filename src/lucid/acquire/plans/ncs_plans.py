"""Custom NCS plans that wrap standard Bluesky plans.

These plans provide simplified interfaces for common use cases,
hiding complexity from users who don't need the full flexibility
of the underlying Bluesky plans.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from bluesky import plan_stubs as bps

if TYPE_CHECKING:
    from ophyd import Device


# =============================================================================
# Camera Acquisition Plans
# =============================================================================

def simple_acquire(
    detector: Device,
    num_images: int = 1,
    acquire_time: float | None = None,
    collect_dark: bool = False,
) -> Generator[Any, Any, Any]:
    """Simple acquisition with optional dark frame collection.

    This plan handles the basic acquisition workflow:
    1. Optionally collect dark frame(s) with shutter closed
    2. Collect light frame(s) with shutter open

    The detector must have cam.shutter_control signal for dark frame support.

    Args:
        detector: Area detector device with cam component.
        num_images: Number of images to acquire.
        acquire_time: Exposure time (uses current setting if None).
        collect_dark: Whether to collect dark frame before light frame.

    Yields:
        Bluesky plan messages.

    Example:
        >>> RE(simple_acquire(det, num_images=5, collect_dark=True))
    """
    # Set acquisition parameters if provided
    if acquire_time is not None:
        yield from bps.mv(detector.cam.acquire_time, acquire_time)

    yield from bps.mv(detector.cam.num_images, num_images)
    yield from bps.mv(detector.cam.image_mode, 0)  # Single mode

    # Collect dark frame if requested
    if collect_dark and hasattr(detector.cam, "shutter_control"):
        # Close shutter
        yield from bps.mv(detector.cam.shutter_control, 0)
        yield from bps.sleep(0.1)  # Allow shutter to close

        # Trigger dark acquisition
        yield from bps.trigger_and_read([detector], name="dark")

        # Open shutter
        yield from bps.mv(detector.cam.shutter_control, 1)
        yield from bps.sleep(0.1)  # Allow shutter to open

    # Collect light frame(s)
    yield from bps.trigger_and_read([detector], name="primary")


def continuous_acquire(
    detector: Device,
    acquire_time: float | None = None,
) -> Generator[Any, Any, Any]:
    """Start continuous acquisition (TV mode).

    Sets image_mode to Continuous and starts acquisition.
    Use bps.mv(detector.cam.acquire, 0) to stop.

    Args:
        detector: Area detector device with cam component.
        acquire_time: Exposure time (uses current setting if None).

    Yields:
        Bluesky plan messages.
    """
    if acquire_time is not None:
        yield from bps.mv(detector.cam.acquire_time, acquire_time)

    yield from bps.mv(detector.cam.image_mode, 2)  # Continuous mode
    yield from bps.mv(detector.cam.acquire, 1)


def stop_acquire(detector: Device) -> Generator[Any, Any, Any]:
    """Stop acquisition.

    Args:
        detector: Area detector device with cam component.

    Yields:
        Bluesky plan messages.
    """
    yield from bps.mv(detector.cam.acquire, 0)


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
    from lucid.acquire.plans.registry import PlanInfo

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

    # Simple Acquire - camera acquisition with optional dark
    plan_info = PlanInfo.from_function(
        name="simple_acquire",
        func=simple_acquire,
        category="acquire",
    )
    plan_info.display_name = "Simple Acquire"
    plan_info.icon = ("#2196F3", "A")  # Blue with "A"
    registry._plans[plan_info.name] = plan_info
    registry._categories.add(plan_info.category)

    # Continuous Acquire - TV mode
    plan_info = PlanInfo.from_function(
        name="continuous_acquire",
        func=continuous_acquire,
        category="acquire",
    )
    plan_info.display_name = "Continuous Acquire"
    plan_info.icon = ("#FF9800", "C")  # Orange with "C"
    registry._plans[plan_info.name] = plan_info
    registry._categories.add(plan_info.category)
