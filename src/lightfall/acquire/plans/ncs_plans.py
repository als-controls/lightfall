"""Custom NCS plans that wrap standard Bluesky plans.

These plans provide simplified interfaces with proper type hints for
automatic UI generation. They replace the raw bluesky builtins (which
use *args and are difficult to generate UIs for).
"""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING, Annotated, Any

from bluesky import plan_stubs as bps
from bluesky import plans as bp

from lucid.ui.annotations import DeviceFilter, Range, Unit

if TYPE_CHECKING:
    pass

# Type aliases
Motor = Any
Detector = Any


def _resolve_target_field(
    detectors: list[Any],
    target_field: str | None,
    plan_name: str = "plan",
) -> str:
    """Resolve a user-supplied target_field to a real detector field name.

    Field names in ``describe()`` vary by device class. This helper
    validates the user's choice against each detector's ``describe()``
    output and applies a few sensible corrections before failing.

    Resolution order:

    1. ``target_field is None`` → first hinted field, else first
       described field.
    2. Exact match in the union of ``describe()`` keys.
    3. Common suffix corrections: ``{target_field}_val``,
       ``{target_field}_value``, ``{target_field}_intensity``.
    4. Unique prefix match (``det2`` → ``det2_centroid_x``).
    5. Otherwise ``ValueError`` listing available fields and the
       closest guess.

    A WARNING is logged whenever resolution succeeds via 3 or 4 so the
    user notices the auto-correction.
    """
    available: dict[str, Any] = {}
    hinted: list[str] = []
    for det in detectors:
        try:
            available.update(det.describe())
        except Exception:
            pass
        try:
            hints = getattr(det, "hints", None)
            fields = hints.get("fields") if isinstance(hints, dict) else None
            if fields:
                hinted.extend(fields)
        except Exception:
            pass

    if not available:
        # describe() unavailable (mock backends, deferred wiring) — trust
        # the user's literal string if provided, else surface a clear
        # error rather than silently failing inside bp.tune_centroid.
        if target_field:
            return target_field
        raise ValueError(
            f"{plan_name}: detectors expose no describable fields and no "
            "target_field was provided"
        )

    available_names = list(available.keys())

    if not target_field:
        for h in hinted:
            if h in available:
                return h
        if available_names:
            return available_names[0]
        raise ValueError(f"{plan_name}: no readable fields on detectors")

    if target_field in available:
        return target_field

    from lucid.utils.logging import logger

    for suffix in ("_val", "_value", "_intensity"):
        candidate = f"{target_field}{suffix}"
        if candidate in available:
            logger.warning(
                "{}: target_field '{}' not found; using '{}' (auto-corrected)",
                plan_name, target_field, candidate,
            )
            return candidate

    prefix_matches = [n for n in available_names if n.startswith(f"{target_field}_")]
    if len(prefix_matches) == 1:
        logger.warning(
            "{}: target_field '{}' not found; using '{}' (unique prefix match)",
            plan_name, target_field, prefix_matches[0],
        )
        return prefix_matches[0]

    closest = prefix_matches[0] if prefix_matches else (
        hinted[0] if hinted and hinted[0] in available else available_names[0]
    )
    raise ValueError(
        f"{plan_name}: target_field '{target_field}' not in detector describe(). "
        f"Available fields: {available_names}. "
        f"Hinted fields: {hinted or '(none)'}. "
        f"Did you mean '{closest}'?"
    )


# =============================================================================
# Camera Acquisition Plans
# =============================================================================

def simple_acquire(
    detector: Annotated[Detector, DeviceFilter(category="detector")],
    num_images: int = 1,
    acquire_time: Annotated[float, Unit("s")] | None = None,
    collect_dark: bool = False,
) -> Generator[Any, Any, Any]:
    """Simple acquisition with optional dark frame collection.

    This plan handles the basic acquisition workflow:
    1. Optionally collect dark frame(s) with shutter closed
    2. Collect light frame(s) with shutter open

    Args:
        detector: Area detector device with cam component.
        num_images: Number of images to acquire.
        acquire_time: Exposure time (uses current setting if None).
        collect_dark: Whether to collect dark frame before light frame.

    Yields:
        Bluesky plan messages.
    """
    if acquire_time is not None:
        yield from bps.mv(detector.cam.acquire_time, acquire_time)

    yield from bps.mv(detector.cam.num_images, num_images)
    yield from bps.mv(detector.cam.image_mode, 0)  # Single mode

    yield from bps.open_run()

    if collect_dark and hasattr(detector.cam, "shutter_control"):
        yield from bps.stage(detector)
        try:
            yield from bps.mv(detector.cam.shutter_control, 0)
            yield from bps.sleep(0.1)
            yield from bps.trigger_and_read([detector], name="dark")
        finally:
            yield from bps.unstage(detector)
        yield from bps.mv(detector.cam.shutter_control, 1)
        yield from bps.sleep(0.1)

    yield from bps.stage(detector)
    try:
        yield from bps.trigger_and_read([detector], name="primary")
    finally:
        yield from bps.unstage(detector)

    yield from bps.close_run()


# =============================================================================
# Count
# =============================================================================

def count(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    num: Annotated[int, Range(min=1)] = 1,
    delay: Annotated[float, Unit("s"), Range(min=0.0)] = 0.0,
) -> Generator[Any, Any, Any]:
    """Take one or more readings from detectors.

    Simply triggers and reads the detectors the specified number of times,
    with an optional delay between readings.

    Args:
        detectors: Detectors to read.
        num: Number of readings to take.
        delay: Delay between readings in seconds.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.count(detectors, num=num, delay=delay)


# =============================================================================
# 1D Scans
# =============================================================================

def scan_1d(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    motor: Annotated[Motor, DeviceFilter(category="motor")],
    start: float,
    stop: float,
    num: Annotated[int, Range(min=1)] = 21,
) -> Generator[Any, Any, Any]:
    """1D scan over a single motor.

    Steps a motor from start to stop in equally-spaced points,
    reading detectors at each position.

    Args:
        detectors: Detectors to read at each point.
        motor: Motor to scan.
        start: Starting position.
        stop: Ending position.
        num: Number of points.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.scan(detectors, motor, start, stop, num)


def rel_scan_1d(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    motor: Annotated[Motor, DeviceFilter(category="motor")],
    start: float,
    stop: float,
    num: Annotated[int, Range(min=1)] = 21,
) -> Generator[Any, Any, Any]:
    """Relative 1D scan over a single motor.

    Like scan_1d, but start and stop are relative to the current
    motor position.

    Args:
        detectors: Detectors to read at each point.
        motor: Motor to scan (offsets relative to current position).
        start: Starting offset from current position.
        stop: Ending offset from current position.
        num: Number of points.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.rel_scan(detectors, motor, start, stop, num)


# =============================================================================
# 2D Scans
# =============================================================================

def scan_2d(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    motor1: Annotated[Motor, DeviceFilter(category="motor")],
    start1: float,
    stop1: float,
    num1: int,
    motor2: Annotated[Motor, DeviceFilter(category="motor")],
    start2: float,
    stop2: float,
    num2: int,
    snake: bool = False,
) -> Generator[Any, Any, Any]:
    """2D grid scan over two motors.

    Scans motor1 (outer loop) and motor2 (inner loop) through a grid
    of positions. Optionally snakes the inner axis for faster scanning.

    Args:
        detectors: Detectors to read at each point.
        motor1: First motor (outer loop).
        start1: Start position for motor1.
        stop1: Stop position for motor1.
        num1: Number of points for motor1.
        motor2: Second motor (inner loop).
        start2: Start position for motor2.
        stop2: Stop position for motor2.
        num2: Number of points for motor2.
        snake: If True, snake the inner axis.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.grid_scan(
        detectors,
        motor1, start1, stop1, num1,
        motor2, start2, stop2, num2,
        snake_axes=snake,
    )


def rel_scan_2d(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    motor1: Annotated[Motor, DeviceFilter(category="motor")],
    start1: float,
    stop1: float,
    num1: int,
    motor2: Annotated[Motor, DeviceFilter(category="motor")],
    start2: float,
    stop2: float,
    num2: int,
    snake: bool = False,
) -> Generator[Any, Any, Any]:
    """Relative 2D grid scan over two motors.

    Like scan_2d, but positions are relative to the current motor positions.

    Args:
        detectors: Detectors to read at each point.
        motor1: First motor (outer loop).
        start1: Start offset for motor1.
        stop1: Stop offset for motor1.
        num1: Number of points for motor1.
        motor2: Second motor (inner loop).
        start2: Start offset for motor2.
        stop2: Stop offset for motor2.
        num2: Number of points for motor2.
        snake: If True, snake the inner axis.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.rel_grid_scan(
        detectors,
        motor1, start1, stop1, num1,
        motor2, start2, stop2, num2,
        snake_axes=snake,
    )


# =============================================================================
# List Scans
# =============================================================================

def list_scan_1d(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    motor: Annotated[Motor, DeviceFilter(category="motor")],
    positions: list[float],
) -> Generator[Any, Any, Any]:
    """Scan a motor through a list of specific positions.

    Unlike a regular scan which uses evenly-spaced points, this plan
    moves to each position in the given list.

    Args:
        detectors: Detectors to read at each point.
        motor: Motor to move.
        positions: List of positions to visit.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.list_scan(detectors, motor, positions)


def rel_list_scan_1d(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    motor: Annotated[Motor, DeviceFilter(category="motor")],
    offsets: list[float],
) -> Generator[Any, Any, Any]:
    """Relative list scan — offsets from current motor position.

    Args:
        detectors: Detectors to read at each point.
        motor: Motor to move (offsets relative to current position).
        offsets: List of offsets from current position.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.rel_list_scan(detectors, motor, offsets)


# =============================================================================
# Adaptive Scan
# =============================================================================

def adaptive_scan(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    target_field: str | None,
    motor: Annotated[Motor, DeviceFilter(category="motor")],
    start: float,
    stop: float,
    min_step: float,
    max_step: float,
    target_delta: float,
    backstep: bool = True,
    threshold: float = 0.8,
) -> Generator[Any, Any, Any]:
    """Adaptive scan that adjusts step size based on signal change.

    Scans a motor while reading a target signal field. The step size
    adapts between min_step and max_step based on how much the target
    signal changes between points.

    Args:
        detectors: Detectors to read (must produce target_field).
        target_field: Field name from the detector's ``describe()`` output,
            or ``None`` to auto-detect from hinted fields.
        motor: Motor to scan.
        start: Starting position.
        stop: Ending position.
        min_step: Minimum step size.
        max_step: Maximum step size.
        target_delta: Target change in signal between points.
        backstep: Whether to allow stepping backward for better resolution.
        threshold: Threshold for step size adjustment (0-1).

    Yields:
        Bluesky plan messages.
    """
    target_field = _resolve_target_field(detectors, target_field, plan_name="adaptive_scan")
    yield from bp.adaptive_scan(
        detectors, target_field, motor, start, stop,
        min_step, max_step, target_delta, backstep,
        threshold=threshold,
    )


# =============================================================================
# Tune Centroid
# =============================================================================

def tune_centroid(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    target_field: str | None,
    motor: Annotated[Motor, DeviceFilter(category="motor")],
    start: float,
    stop: float,
    min_step: float,
    num: Annotated[int, Range(min=2)] = 10,
    step_factor: float = 3.0,
    snake: bool = False,
) -> Generator[Any, Any, Any]:
    """Iteratively tune a motor to center on a signal peak.

    Performs repeated scans, each time narrowing the range around the
    centroid of the target signal until the step size reaches min_step.

    Args:
        detectors: Detectors to read (must produce target_field).
        target_field: Field name from the detector's ``describe()`` output,
            or ``None`` to auto-detect from hinted fields.
        motor: Motor to tune.
        start: Initial start position.
        stop: Initial stop position.
        min_step: Minimum step size (convergence criterion).
        num: Number of points per iteration.
        step_factor: Factor by which to narrow the range each iteration.
        snake: Whether to alternate scan direction.

    Yields:
        Bluesky plan messages.
    """
    target_field = _resolve_target_field(detectors, target_field, plan_name="tune_centroid")
    yield from bp.tune_centroid(
        detectors, target_field, motor, start, stop,
        min_step, num=num, step_factor=step_factor, snake=snake,
    )


def tune_centroid_2d(
    detectors: Annotated[list[Detector], DeviceFilter(category="detector")],
    target_field: str | None,
    motor1: Annotated[Motor, DeviceFilter(category="motor")],
    start1: float,
    stop1: float,
    min_step1: float,
    motor2: Annotated[Motor, DeviceFilter(category="motor")],
    start2: float,
    stop2: float,
    min_step2: float,
    num: Annotated[int, Range(min=2)] = 10,
    step_factor: float = 3.0,
    snake: bool = False,
    max_iterations: Annotated[int, Range(min=1, max=50)] = 10,
) -> Generator[Any, Any, Any]:
    """Iteratively tune two motors to center on a 2D signal peak.

    Alternates tuning motor1 and motor2, each time centering on the
    signal peak, until both converge or max_iterations is reached.

    Args:
        detectors: Detectors to read (must produce target_field).
        target_field: Field name from the detector's ``describe()`` output,
            or ``None`` to auto-detect from hinted fields.
        motor1: First motor to tune.
        start1: Initial start for motor1.
        stop1: Initial stop for motor1.
        min_step1: Min step for motor1 convergence.
        motor2: Second motor to tune.
        start2: Initial start for motor2.
        stop2: Initial stop for motor2.
        min_step2: Min step for motor2 convergence.
        num: Number of points per scan iteration.
        step_factor: Factor to narrow range each iteration.
        snake: Whether to alternate scan direction.
        max_iterations: Maximum total tuning iterations.

    Yields:
        Bluesky plan messages.
    """
    target_field = _resolve_target_field(detectors, target_field, plan_name="tune_centroid_2d")
    for _ in range(max_iterations):
        # Tune motor1
        yield from bp.tune_centroid(
            detectors, target_field, motor1, start1, stop1,
            min_step1, num=num, step_factor=step_factor, snake=snake,
        )
        # Tune motor2
        yield from bp.tune_centroid(
            detectors, target_field, motor2, start2, stop2,
            min_step2, num=num, step_factor=step_factor, snake=snake,
        )


# =============================================================================
# Registration
# =============================================================================

def _register(registry, name, func, category, display_name, icon):
    """Helper to register a plan with display metadata."""
    from lucid.acquire.plans.registry import PlanInfo

    plan_info = PlanInfo.from_function(name=name, func=func, category=category)
    plan_info.display_name = display_name
    plan_info.icon = icon
    registry._plans[plan_info.name] = plan_info
    registry._categories.add(plan_info.category)


def register_ncs_plans(registry) -> None:
    """Register custom NCS plans in the given registry.

    Args:
        registry: PlanRegistry to register plans in.
    """
    # Count
    _register(registry, "count", count, "count",
              "Count", ("#2196F3", "C"))

    # 1D Scans
    _register(registry, "scan_1d", scan_1d, "scan",
              "1D Scan", ("#4CAF50", "1"))
    _register(registry, "rel_scan_1d", rel_scan_1d, "scan",
              "Relative 1D Scan", ("#4CAF50", "R"))

    # 2D Scans
    _register(registry, "scan_2d", scan_2d, "scan",
              "2D Grid Scan", ("#4CAF50", "2"))
    _register(registry, "rel_scan_2d", rel_scan_2d, "scan",
              "Relative 2D Grid Scan", ("#4CAF50", "r"))

    # List Scans
    _register(registry, "list_scan_1d", list_scan_1d, "scan",
              "List Scan", ("#9C27B0", "L"))
    _register(registry, "rel_list_scan_1d", rel_list_scan_1d, "scan",
              "Relative List Scan", ("#9C27B0", "l"))

    # Adaptive
    _register(registry, "adaptive_scan", adaptive_scan, "scan",
              "Adaptive Scan", ("#FF9800", "A"))

    # Alignment
    _register(registry, "tune_centroid", tune_centroid, "alignment",
              "Tune Centroid", ("#FF9800", "T"))
    _register(registry, "tune_centroid_2d", tune_centroid_2d, "alignment",
              "Tune Centroid 2D", ("#FF9800", "2"))

    # Camera Acquisition
    _register(registry, "simple_acquire", simple_acquire, "acquire",
              "Simple Acquire", ("#2196F3", "A"))

    # Adaptive experiment (Tsuchinoko coordination)
    try:
        from lucid.acquire.plans.adaptive import adaptive_experiment

        registry.register(
            "adaptive_experiment", adaptive_experiment, category="scan"
        )
    except ImportError as e:
        from lucid.utils.logging import logger
        logger.debug(f"Could not register adaptive_experiment: {e}")
