"""Example plans translated from Xi-CAM to use LUCID annotations.

These plans demonstrate the use of Annotated type hints with LUCID's
annotation metadata classes for procedural UI generation.

Usage:
    from lightfall.acquire.plans.example_plans import register_example_plans
    register_example_plans(registry)
"""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING, Annotated, Any

import numpy as np
from bluesky import plan_stubs as bps
from bluesky import plans as bp
from bluesky import utils as bsu

from lightfall.ui.annotations import (
    Decimals,
    Default,
    DeviceDefault,
    DeviceFilter,
    Unit,
)

if TYPE_CHECKING:
    from lightfall.acquire.plans.registry import PlanRegistry


# Type aliases for clarity
Device = Any
Motor = Any
Detector = Any


# =============================================================================
# Plan 0: 2D Grid Scan with Wait Per Step
# =============================================================================

def grid_scan_2d_wait(
    detectors: Annotated[
        list[Detector],
        DeviceDefault("PI_MTE3"),
    ],
    motor1: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_lift"),
    ],
    motor2: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_translate"),
    ],
    axis_1_min: float,
    axis_1_max: float,
    axis_1_steps: int,
    axis_2_min: float,
    axis_2_max: float,
    axis_2_steps: int,
    wait_time: Annotated[float, Unit("s"), Default(0.5)] = 0.5,
) -> Generator[Any, Any, Any]:
    """2D grid scan with configurable wait time per step.

    Scans motor1 and motor2 through a grid pattern, waiting at each point
    before taking a reading.

    Args:
        detectors: Detectors to read at each point.
        motor1: First scan axis (outer loop).
        motor2: Second scan axis (inner loop).
        axis_1_min: Minimum position for motor1.
        axis_1_max: Maximum position for motor1.
        axis_1_steps: Number of steps for motor1.
        axis_2_min: Minimum position for motor2.
        axis_2_max: Maximum position for motor2.
        axis_2_steps: Number of steps for motor2.
        wait_time: Time to wait at each position before reading.

    Yields:
        Bluesky plan messages.
    """
    def wait_per_step(detectors, step, pos_cache):
        yield from bps.sleep(wait_time)
        yield from bps.one_nd_step(detectors, step, pos_cache)

    yield from bp.grid_scan(
        detectors,
        motor1, axis_1_min, axis_1_max, axis_1_steps,
        motor2, axis_2_min, axis_2_max, axis_2_steps,
        per_step=wait_per_step,
    )


# =============================================================================
# Plan 1: Generic 1D Scan
# =============================================================================

def scan_1d(
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
    """Generic 1D scan over a single motor.

    Args:
        detectors: Detectors to read at each point.
        motor: Motor to scan.
        start: Starting position.
        stop: Ending position.
        num_points: Number of points in the scan.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.scan(detectors, motor, start, stop, num_points)


# =============================================================================
# Plan 2: 2D Grid Scan with Area Detector Filter
# =============================================================================

def grid_scan_2d(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    motor1: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_lift"),
    ],
    motor2: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_translate"),
    ],
    axis_1_min: float,
    axis_1_max: float,
    axis_1_steps: int,
    axis_2_min: float,
    axis_2_max: float,
    axis_2_steps: int,
) -> Generator[Any, Any, Any]:
    """2D grid scan with area detector.

    Args:
        detectors: Area detectors to read.
        motor1: First scan axis.
        motor2: Second scan axis.
        axis_1_min: Minimum position for motor1.
        axis_1_max: Maximum position for motor1.
        axis_1_steps: Number of steps for motor1.
        axis_2_min: Minimum position for motor2.
        axis_2_max: Maximum position for motor2.
        axis_2_steps: Number of steps for motor2.

    Yields:
        Bluesky plan messages.
    """
    def wait_per_step(detectors, step, pos_cache):
        yield from bps.sleep(0.5)
        yield from bps.one_nd_step(detectors, step, pos_cache)

    yield from bp.grid_scan(
        detectors,
        motor1, axis_1_min, axis_1_max, axis_1_steps,
        motor2, axis_2_min, axis_2_max, axis_2_steps,
        per_step=wait_per_step,
    )


# =============================================================================
# Plan 3: EPU Polarization Scan with Sleep and Dark Collection
# =============================================================================

def polarization_scan(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    polarization_motor: Annotated[
        Motor,
        DeviceDefault("EPU_Polarization"),
    ],
    pol_min: float,
    pol_max: float,
    num_steps: int,
    sleep_time: Annotated[float, Unit("s"), Default(10.0)] = 10.0,
    collect_darks: bool = False,
) -> Generator[Any, Any, Any]:
    """EPU polarization scan with configurable sleep time.

    Scans through polarization values with a sleep time at each point
    to allow the EPU to stabilize. Optionally collects dark frames.

    Args:
        detectors: Detectors to read.
        polarization_motor: EPU polarization motor.
        pol_min: Minimum polarization value.
        pol_max: Maximum polarization value.
        num_steps: Number of polarization steps.
        sleep_time: Time to wait after moving polarization.
        collect_darks: Whether to collect dark frames at each point.

    Yields:
        Bluesky plan messages.
    """
    def sleepy_trigger(dets, name="primary"):
        yield from bps.sleep(sleep_time)
        yield from bps.trigger_and_read(dets, name=name)

        if collect_darks:
            # Note: Dark collection logic depends on detector type
            # This is a simplified version
            yield from bps.trigger_and_read(dets, name="dark")

    def sleepy_step(detectors, step, pos_cache):
        yield from bps.one_nd_step(detectors, step, pos_cache, take_reading=sleepy_trigger)

    yield from bp.scan(
        detectors,
        polarization_motor, pol_min, pol_max, num_steps,
        per_step=sleepy_step,
    )


# =============================================================================
# Plan 4: Energy Scan
# =============================================================================

def energy_scan(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    energy_motor: Annotated[
        Motor,
        DeviceDefault("mono_energy"),
    ],
    energy_min: Annotated[float, Unit("eV")],
    energy_max: Annotated[float, Unit("eV")],
    num_steps: int,
) -> Generator[Any, Any, Any]:
    """Energy scan using the monochromator.

    Args:
        detectors: Detectors to read.
        energy_motor: Monochromator energy motor.
        energy_min: Starting energy in eV.
        energy_max: Ending energy in eV.
        num_steps: Number of energy points.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.scan(detectors, energy_motor, energy_min, energy_max, num_steps)


# =============================================================================
# Plan 5: Energy Scan with Sleep and Dark Collection
# =============================================================================

def energy_scan_with_darks(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    energy_motor: Annotated[
        Motor,
        DeviceDefault("beamline_energy"),
    ],
    energy_min: Annotated[float, Unit("eV")],
    energy_max: Annotated[float, Unit("eV")],
    num_steps: int,
    sleep_time: Annotated[float, Unit("s"), Default(10.0)] = 10.0,
    collect_darks: bool = False,
) -> Generator[Any, Any, Any]:
    """Energy scan with sleep time and optional dark collection.

    Args:
        detectors: Detectors to read.
        energy_motor: Beamline energy motor.
        energy_min: Starting energy in eV.
        energy_max: Ending energy in eV.
        num_steps: Number of energy points.
        sleep_time: Time to wait after energy move.
        collect_darks: Whether to collect dark frames.

    Yields:
        Bluesky plan messages.
    """
    def sleepy_trigger(dets, name="primary"):
        yield from bps.sleep(sleep_time)
        yield from bps.trigger_and_read(dets, name=name)

        if collect_darks:
            yield from bps.trigger_and_read(dets, name="dark")

    def sleepy_step(detectors, step, pos_cache):
        yield from bps.one_nd_step(detectors, step, pos_cache, take_reading=sleepy_trigger)

    yield from bp.scan(
        detectors,
        energy_motor, energy_min, energy_max, num_steps,
        per_step=sleepy_step,
    )


# =============================================================================
# Plan 6: Fermat Spiral Scan (Absolute)
# =============================================================================

def _fermat_spiral(num_points: int, step_size: float, ordered: bool = True) -> np.ndarray:
    """Generate Fermat spiral positions.

    Args:
        num_points: Number of points in spiral.
        step_size: Step size in um.
        ordered: Whether to order by radial distance.

    Returns:
        Array of (x, y) positions.
    """
    offset = np.random.rand() * 0.5
    theta_offset = np.random.rand() * 2 * np.pi

    ns = offset + np.arange(num_points)
    rs = (step_size / np.sqrt(np.pi)) * np.sqrt(ns)
    golden_angle = np.pi * (1 + np.sqrt(5))
    thetas = ns * golden_angle + theta_offset

    xs = rs * np.sin(thetas)
    ys = rs * np.cos(thetas)

    scan_positions = np.array([xs, ys]).transpose()

    if ordered:
        radial_indices = np.ceil(np.sqrt(ns) * 0.628)
        thetas = np.mod(thetas, 2 * np.pi)
        scan_positions = scan_positions[np.argsort(radial_indices * 7 + thetas), :]

    return scan_positions


def fermat_spiral_scan(
    detectors: Annotated[
        list[Detector],
        DeviceDefault("PI_MTE3"),
    ],
    motor1: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_lift"),
    ],
    motor2: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_translate"),
    ],
    center_x: Annotated[float, Unit("um"), Decimals(7)],
    center_y: Annotated[float, Unit("um"), Decimals(7)],
    num_points: int,
    step_size: Annotated[float, Unit("um"), Decimals(4)],
    delay_motor1: Annotated[float, Unit("s"), Decimals(4), Default(3.0)] = 3.0,
    delay_motor2: Annotated[float, Unit("s"), Decimals(4), Default(3.0)] = 3.0,
    collect_darks: bool = False,
) -> Generator[Any, Any, Any]:
    """Fermat spiral scan centered at given position.

    Scans in a Fermat spiral pattern, moving motors sequentially with
    configurable delays. Optionally collects dark frames at each point.

    Args:
        detectors: Detectors to read.
        motor1: First motor (typically vertical/lift).
        motor2: Second motor (typically horizontal/translate).
        center_x: X center position in um.
        center_y: Y center position in um.
        num_points: Number of points in spiral.
        step_size: Spiral step size in um.
        delay_motor1: Delay after moving motor1.
        delay_motor2: Delay after moving motor2.
        collect_darks: Whether to collect dark frames.

    Yields:
        Bluesky plan messages.
    """
    positions = _fermat_spiral(num_points, step_size)
    positions = positions + np.array([center_x, center_y])

    def sequential_step(detectors, step, pos_cache):
        """Move motors sequentially with delays."""
        yield bsu.Msg("checkpoint")
        grp = bps._short_uid("set")

        sleep_times = [delay_motor1, delay_motor2]
        for (motor, pos), sleep_time in zip(step.items(), sleep_times, strict=False):
            if pos == pos_cache[motor]:
                continue
            yield bsu.Msg("set", motor, pos, group=grp)
            yield bsu.Msg("wait", None, group=grp)
            yield from bps.sleep(sleep_time)
            pos_cache[motor] = pos

        yield from bps.trigger_and_read(list(detectors) + list(step.keys()))

        if collect_darks:
            yield from bps.trigger_and_read(list(detectors) + list(step.keys()), name="dark")

    yield from bp.list_scan(
        detectors,
        motor1, positions[:, 0].tolist(),
        motor2, positions[:, 1].tolist(),
        per_step=sequential_step,
    )


# =============================================================================
# Plan 7: Simple 2D Grid Scan
# =============================================================================

def simple_grid_scan(
    detectors: Annotated[
        list[Detector],
        DeviceDefault("PI_MTE3"),
    ],
    motor1: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_lift"),
    ],
    motor2: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_translate"),
    ],
    axis_1_min: float,
    axis_1_max: float,
    axis_1_steps: int,
    axis_2_min: float,
    axis_2_max: float,
    axis_2_steps: int,
) -> Generator[Any, Any, Any]:
    """Simple 2D grid scan without extra options.

    Args:
        detectors: Detectors to read.
        motor1: First scan axis.
        motor2: Second scan axis.
        axis_1_min: Minimum for axis 1.
        axis_1_max: Maximum for axis 1.
        axis_1_steps: Steps for axis 1.
        axis_2_min: Minimum for axis 2.
        axis_2_max: Maximum for axis 2.
        axis_2_steps: Steps for axis 2.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.grid_scan(
        detectors,
        motor1, axis_1_min, axis_1_max, axis_1_steps,
        motor2, axis_2_min, axis_2_max, axis_2_steps,
    )


# =============================================================================
# Plan 8: Temperature List Scan with Dark Collection
# =============================================================================

def temperature_list_scan(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    temperature_motor: Annotated[
        Motor,
        DeviceDefault("LS_LLHTA"),
    ],
    temperatures: Annotated[list[float], Default([26.9, 26.8, 26.6])],
    sleep_time: Annotated[float, Unit("s"), Default(10.0)] = 10.0,
    collect_darks: bool = False,
) -> Generator[Any, Any, Any]:
    """Scan through a list of temperatures.

    Args:
        detectors: Detectors to read.
        temperature_motor: Temperature controller.
        temperatures: List of temperature setpoints.
        sleep_time: Time to wait at each temperature.
        collect_darks: Whether to collect dark frames.

    Yields:
        Bluesky plan messages.
    """
    def sleepy_trigger(dets, name="primary"):
        yield from bps.sleep(sleep_time)
        yield from bps.trigger_and_read(dets, name=name)

        if collect_darks:
            yield from bps.trigger_and_read(dets, name="dark")

    def sleepy_step(detectors, step, pos_cache):
        yield from bps.one_nd_step(detectors, step, pos_cache, take_reading=sleepy_trigger)

    yield from bp.list_scan(
        detectors,
        temperature_motor, temperatures,
        per_step=sleepy_step,
    )


# =============================================================================
# Plan 9: Relative Fermat Spiral Scan
# =============================================================================

def fermat_spiral_rel_scan(
    detectors: Annotated[
        list[Detector],
        DeviceDefault("fastccd"),
    ],
    motor1: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_lift"),
    ],
    motor2: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_translate"),
    ],
    num_points: int,
    step_size: Annotated[float, Unit("um"), Decimals(4)],
    delay_motor1: Annotated[float, Unit("s"), Decimals(4), Default(3.0)] = 3.0,
    delay_motor2: Annotated[float, Unit("s"), Decimals(4), Default(3.0)] = 3.0,
    collect_darks: bool = False,
) -> Generator[Any, Any, Any]:
    """Relative Fermat spiral scan from current position.

    Like fermat_spiral_scan but positions are relative to current motor positions.

    Args:
        detectors: Detectors to read.
        motor1: First motor.
        motor2: Second motor.
        num_points: Number of points in spiral.
        step_size: Spiral step size in um.
        delay_motor1: Delay after moving motor1.
        delay_motor2: Delay after moving motor2.
        collect_darks: Whether to collect dark frames.

    Yields:
        Bluesky plan messages.
    """
    positions = _fermat_spiral(num_points, step_size)

    def sequential_step(detectors, step, pos_cache):
        yield bsu.Msg("checkpoint")
        grp = bps._short_uid("set")

        sleep_times = [delay_motor1, delay_motor2]
        for (motor, pos), sleep_time in zip(step.items(), sleep_times, strict=False):
            if pos == pos_cache[motor]:
                continue
            yield bsu.Msg("set", motor, pos, group=grp)
            yield bsu.Msg("wait", None, group=grp)
            yield from bps.sleep(sleep_time)
            pos_cache[motor] = pos

        yield from bps.trigger_and_read(list(detectors) + list(step.keys()))

        if collect_darks:
            yield from bps.trigger_and_read(list(detectors) + list(step.keys()), name="dark")

    yield from bp.rel_list_scan(
        detectors,
        motor1, positions[:, 0].tolist(),
        motor2, positions[:, 1].tolist(),
        per_step=sequential_step,
    )


# =============================================================================
# Plan 10: Simple Grid Scan with Area Detector Filter
# =============================================================================

def grid_scan_areadet(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    motor1: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_lift"),
    ],
    motor2: Annotated[
        Motor,
        DeviceFilter(device_class="EpicsMotor"),
        DeviceDefault("sample_translate"),
    ],
    axis_1_min: float,
    axis_1_max: float,
    axis_1_steps: int,
    axis_2_min: float,
    axis_2_max: float,
    axis_2_steps: int,
) -> Generator[Any, Any, Any]:
    """2D grid scan with area detector filter.

    Args:
        detectors: Area detectors to read.
        motor1: First scan axis.
        motor2: Second scan axis.
        axis_1_min: Minimum for axis 1.
        axis_1_max: Maximum for axis 1.
        axis_1_steps: Steps for axis 1.
        axis_2_min: Minimum for axis 2.
        axis_2_max: Maximum for axis 2.
        axis_2_steps: Steps for axis 2.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.grid_scan(
        detectors,
        motor1, axis_1_min, axis_1_max, axis_1_steps,
        motor2, axis_2_min, axis_2_max, axis_2_steps,
    )


# =============================================================================
# Plan 11: Temperature Scan with Dark Collection
# =============================================================================

def temperature_scan(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    temperature_motor: Annotated[
        Motor,
        DeviceDefault("LS_LLHTA"),
    ],
    temp_min: Annotated[float, Unit("K")],
    temp_max: Annotated[float, Unit("K")],
    num_steps: int,
    sleep_time: Annotated[float, Unit("s"), Default(10.0)] = 10.0,
    collect_darks: bool = False,
) -> Generator[Any, Any, Any]:
    """Temperature scan with dark collection.

    Args:
        detectors: Detectors to read.
        temperature_motor: Temperature controller.
        temp_min: Starting temperature in Kelvin.
        temp_max: Ending temperature in Kelvin.
        num_steps: Number of temperature points.
        sleep_time: Time to wait at each temperature.
        collect_darks: Whether to collect dark frames.

    Yields:
        Bluesky plan messages.
    """
    def sleepy_trigger(dets, name="primary"):
        yield from bps.sleep(sleep_time)
        yield from bps.trigger_and_read(dets, name=name)

        if collect_darks:
            yield from bps.trigger_and_read(dets, name="dark")

    def sleepy_step(detectors, step, pos_cache):
        yield from bps.one_nd_step(detectors, step, pos_cache, take_reading=sleepy_trigger)

    yield from bp.scan(
        detectors,
        temperature_motor, temp_min, temp_max, num_steps,
        per_step=sleepy_step,
    )


# =============================================================================
# Plan 12: Temperature-Polarization-Energy Scan
# =============================================================================

def temp_pol_energy_scan(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    temperature_motor: Annotated[Motor, DeviceDefault("LS_LLHTA")],
    polarization_motor: Annotated[Motor, DeviceDefault("EPU_Polarization")],
    energy_motor: Annotated[Motor, DeviceDefault("mono_energy")],
    temp_min: Annotated[float, Unit("K")],
    temp_max: Annotated[float, Unit("K")],
    temp_steps: int,
    temp_sleep: Annotated[float, Unit("s")],
    pol_min: float,
    pol_max: float,
    pol_steps: int,
    energy_min: Annotated[float, Unit("eV")],
    energy_max: Annotated[float, Unit("eV")],
    energy_steps: int,
) -> Generator[Any, Any, Any]:
    """Combined temperature, polarization, and energy scan.

    For each temperature, performs a 2D grid scan over polarization and energy.

    Args:
        detectors: Detectors to read.
        temperature_motor: Temperature controller.
        polarization_motor: EPU polarization motor.
        energy_motor: Monochromator energy motor.
        temp_min: Starting temperature in K.
        temp_max: Ending temperature in K.
        temp_steps: Number of temperature points.
        temp_sleep: Sleep time at each temperature.
        pol_min: Minimum polarization.
        pol_max: Maximum polarization.
        pol_steps: Number of polarization points.
        energy_min: Minimum energy in eV.
        energy_max: Maximum energy in eV.
        energy_steps: Number of energy points.

    Yields:
        Bluesky plan messages.
    """
    for T in np.linspace(temp_min, temp_max, temp_steps):
        yield from bps.mv(temperature_motor, T)
        yield from bps.sleep(temp_sleep)
        yield from bp.grid_scan(
            detectors,
            polarization_motor, pol_min, pol_max, pol_steps,
            energy_motor, energy_min, energy_max, energy_steps,
        )


# =============================================================================
# Plan 13: Theta-2Theta Scan
# =============================================================================

def theta_two_theta_scan(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    theta_motor: Annotated[Motor, DeviceDefault("sample_rotate_steppertheta")],
    two_theta_motor: Annotated[Motor, DeviceDefault("detector_rotate")],
    two_theta_min: Annotated[float, Unit("°")],
    two_theta_max: Annotated[float, Unit("°")],
    num_steps: int,
) -> Generator[Any, Any, Any]:
    """Theta-2theta coupled scan.

    Scans 2theta while keeping theta at half the 2theta value.

    Args:
        detectors: Detectors to read.
        theta_motor: Sample theta motor.
        two_theta_motor: Detector 2theta motor.
        two_theta_min: Minimum 2theta angle in degrees.
        two_theta_max: Maximum 2theta angle in degrees.
        num_steps: Number of scan points.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.scan(
        detectors,
        two_theta_motor, two_theta_min, two_theta_max,
        theta_motor, two_theta_min / 2, two_theta_max / 2,
        num_steps,
    )


# =============================================================================
# Plan 14: Multi-Motor List Scan with Wait
# =============================================================================

def multi_motor_list_scan(
    detectors: Annotated[
        list[Detector],
        DeviceDefault("PI_MTE3"),
    ],
    temperature_motor: Annotated[Motor, DeviceDefault("LS_LLHTA")],
    theta_motor: Annotated[Motor, DeviceDefault("sample_rotate_steppertheta")],
    two_theta_motor: Annotated[Motor, DeviceDefault("detector_rotate")],
    temperatures: Annotated[list[float], Default([65, 66, 67, 68])],
    theta_values: Annotated[list[float], Default([41.4, 41.4, 41.4, 41.4])],
    two_theta_values: Annotated[list[float], Default([59.3, 59.3, 59.3, 59.3])],
    wait_time: Annotated[float, Unit("s"), Default(300)] = 300,
) -> Generator[Any, Any, Any]:
    """Multi-motor list scan with wait time at each point.

    Scans temperature, theta, and 2theta together through coordinated lists.

    Args:
        detectors: Detectors to read.
        temperature_motor: Temperature controller.
        theta_motor: Sample theta motor.
        two_theta_motor: Detector 2theta motor.
        temperatures: List of temperature setpoints.
        theta_values: List of theta positions.
        two_theta_values: List of 2theta positions.
        wait_time: Time to wait at each point in seconds.

    Yields:
        Bluesky plan messages.
    """
    def wait_per_step(detectors, step, pos_cache):
        yield from bps.sleep(wait_time)
        yield from bps.one_nd_step(detectors, step, pos_cache)

    yield from bp.list_scan(
        detectors,
        temperature_motor, temperatures,
        theta_motor, theta_values,
        two_theta_motor, two_theta_values,
        per_step=wait_per_step,
    )


# =============================================================================
# Plan 15: Temperature Scan with Simultaneous LabVIEW
# =============================================================================

def temperature_scan_simultaneous(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    temperature_motor: Annotated[Motor, DeviceDefault("LS_LLHTA")],
    temp_min: Annotated[float, Unit("K")],
    temp_max: Annotated[float, Unit("K")],
    num_steps: int,
    sleep_time: Annotated[float, Unit("s"), Default(10.0)] = 10.0,
    collect_darks: bool = False,
    simultaneous_labview: bool = False,
) -> Generator[Any, Any, Any]:
    """Temperature scan with optional simultaneous LabVIEW acquisition.

    Args:
        detectors: Primary detectors to read.
        temperature_motor: Temperature controller.
        temp_min: Starting temperature in K.
        temp_max: Ending temperature in K.
        num_steps: Number of temperature points.
        sleep_time: Time to wait at each temperature.
        collect_darks: Whether to collect dark frames.
        simultaneous_labview: Whether to collect LabVIEW data during exposure.

    Yields:
        Bluesky plan messages.
    """
    def sleepy_trigger(dets, name="primary"):
        yield from bps.sleep(sleep_time)
        # Note: simultaneous_labview would require additional labview device handling
        yield from bps.trigger_and_read(dets, name=name)

        if collect_darks:
            yield from bps.trigger_and_read(dets, name="dark")

    def sleepy_step(detectors, step, pos_cache):
        yield from bps.one_nd_step(detectors, step, pos_cache, take_reading=sleepy_trigger)

    yield from bp.scan(
        detectors,
        temperature_motor, temp_min, temp_max, num_steps,
        per_step=sleepy_step,
    )


# =============================================================================
# Plan 16: Field-Polarization Remnant Scan
# =============================================================================

def field_remnant_scan(
    detectors: Annotated[
        list[Detector],
        DeviceFilter(group="areadetectors"),
        DeviceDefault("PI_MTE3"),
    ],
    field_motor: Annotated[
        Motor,
        DeviceFilter(group="magnets"),
    ],
    polarization_motor: Annotated[Motor, DeviceDefault("EPU_Polarization")],
    field_min: Annotated[float, Unit("G"), Default(0.1)] = 0.1,
    field_max: Annotated[float, Unit("G"), Default(500)] = 500,
    field_steps: Annotated[int, Default(6)] = 6,
    field_sleep: Annotated[float, Unit("s"), Default(3.0)] = 3.0,
    pol_min: Annotated[float, Default(-1)] = -1,
    pol_max: Annotated[float, Default(1)] = 1,
    pol_steps: Annotated[int, Default(2)] = 2,
) -> Generator[Any, Any, Any]:
    """Field remnant scan with polarization.

    For each field value:
    1. Set field to value, wait
    2. Set field to near-zero (0.1 G), wait
    3. Scan through polarizations

    This measures magnetic remnant effects at different field strengths.

    Args:
        detectors: Detectors to read.
        field_motor: Magnetic field motor.
        polarization_motor: EPU polarization motor.
        field_min: Minimum field in Gauss.
        field_max: Maximum field in Gauss.
        field_steps: Number of field points.
        field_sleep: Sleep time at each field.
        pol_min: Minimum polarization.
        pol_max: Maximum polarization.
        pol_steps: Number of polarization points.

    Yields:
        Bluesky plan messages.
    """
    for field in np.linspace(field_min, field_max, field_steps):
        yield from bps.mv(field_motor, field)
        yield from bps.sleep(field_sleep)
        yield from bps.mv(field_motor, 0.1)  # Near-zero remnant
        yield from bps.sleep(field_sleep)
        yield from bp.scan(
            detectors,
            polarization_motor, pol_min, pol_max, pol_steps,
        )


# =============================================================================
# Registration
# =============================================================================

def register_example_plans(registry: PlanRegistry) -> None:
    """Register all example plans in the given registry.

    Args:
        registry: PlanRegistry to register plans in.
    """
    from lightfall.acquire.plans.registry import PlanInfo

    plans_to_register = [
        ("grid_scan_2d_wait", grid_scan_2d_wait, "scan", "2D Grid Scan (Wait)", ("#4CAF50", "G")),
        ("scan_1d", scan_1d, "scan", "1D Scan", ("#4CAF50", "1")),
        ("grid_scan_2d", grid_scan_2d, "scan", "2D Grid Scan", ("#4CAF50", "2")),
        ("polarization_scan", polarization_scan, "scan", "Polarization Scan", ("#9C27B0", "P")),
        ("energy_scan", energy_scan, "scan", "Energy Scan", ("#2196F3", "E")),
        ("energy_scan_with_darks", energy_scan_with_darks, "scan", "Energy Scan (Darks)", ("#2196F3", "D")),
        ("fermat_spiral_scan", fermat_spiral_scan, "scan", "Fermat Spiral", ("#FF9800", "F")),
        ("simple_grid_scan", simple_grid_scan, "scan", "Simple Grid Scan", ("#4CAF50", "S")),
        ("temperature_list_scan", temperature_list_scan, "scan", "Temperature List Scan", ("#F44336", "T")),
        ("fermat_spiral_rel_scan", fermat_spiral_rel_scan, "scan", "Fermat Spiral (Rel)", ("#FF9800", "R")),
        ("grid_scan_areadet", grid_scan_areadet, "scan", "Grid Scan (AreaDet)", ("#4CAF50", "A")),
        ("temperature_scan", temperature_scan, "scan", "Temperature Scan", ("#F44336", "T")),
        ("temp_pol_energy_scan", temp_pol_energy_scan, "scan", "Temp-Pol-Energy Scan", ("#E91E63", "3")),
        ("theta_two_theta_scan", theta_two_theta_scan, "scan", "Theta-2Theta Scan", ("#795548", "θ")),
        ("multi_motor_list_scan", multi_motor_list_scan, "scan", "Multi-Motor List", ("#607D8B", "M")),
        ("temperature_scan_simultaneous", temperature_scan_simultaneous, "scan", "Temp Scan (Simul)", ("#F44336", "S")),
        ("field_remnant_scan", field_remnant_scan, "scan", "Field Remnant Scan", ("#3F51B5", "B")),
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
