"""Type annotation metadata for procedural UI generation.

Provides dataclasses that can be used with `typing.Annotated` to add
UI hints for plan parameters. These annotations control how the
parameter editor renders inputs for plan functions.

Usage:
    from typing import Annotated
    from lucid.ui.annotations import Unit, Decimals, Range, DeviceFilter

    def scan(
        energy: Annotated[float, Unit("eV"), Range(0, 10000)],
        num_points: Annotated[int, Range(1, 1000)],
        motor: Annotated[Device, DeviceFilter(device_class="EpicsMotor")],
    ): ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Unit:
    """Unit/suffix to display next to numeric input.

    The suffix is displayed after the input field to indicate the unit
    of measurement.

    Args:
        suffix: Unit string to display (e.g., "eV", "s", "K", "mm").

    Example:
        energy: Annotated[float, Unit("eV")] = 100.0
    """

    suffix: str


@dataclass(frozen=True)
class Decimals:
    """Number of decimal places for float display.

    Controls the precision of the spinbox widget for float parameters.

    Args:
        places: Number of decimal places (e.g., 4 for 0.0001 precision).

    Example:
        step_size: Annotated[float, Decimals(4)] = 0.001
    """

    places: int


@dataclass(frozen=True)
class Range:
    """Min/max bounds for numeric input.

    Sets the allowed range for numeric parameters in the spinbox widget.

    Args:
        min: Minimum allowed value (None for no minimum).
        max: Maximum allowed value (None for no maximum).

    Example:
        num_points: Annotated[int, Range(1, 1000)] = 10
    """

    min: float | int | None = None
    max: float | int | None = None


@dataclass(frozen=True)
class Default:
    """Default value for parameter.

    Provides a default value that overrides the function signature default.
    Useful when the annotation-based default differs from the code default.

    Args:
        value: The default value for this parameter.

    Example:
        exposure: Annotated[float, Default(1.0), Unit("s")]
    """

    value: Any


@dataclass(frozen=True)
class DeviceFilter:
    """Filter criteria for device selection.

    All specified criteria use AND logic within a single filter.
    Use DeviceFilterAny to combine filters with OR logic.

    Args:
        device_class: Match ophyd class name (e.g., "EpicsMotor", "AreaDetector").
        category: Match device category (e.g., "motor", "detector").
        group: Match device group from tags (e.g., "areadetectors", "magnets").
        source: Match device source/connection type (e.g., "epics", "simulated").
        name_pattern: Regex pattern for device name matching.

    Example:
        motor: Annotated[Device, DeviceFilter(device_class="EpicsMotor")]
        detector: Annotated[Device, DeviceFilter(category="detector", group="areadetectors")]
    """

    device_class: str | None = None
    category: str | set[str] | None = None
    group: str | None = None
    source: str | None = None
    name_pattern: str | None = None


@dataclass(frozen=True)
class DeviceFilterAny:
    """Combine multiple DeviceFilter with OR logic.

    Allows selecting devices that match ANY of the specified filters,
    enabling disjoint filter criteria (e.g., "motors OR detectors").

    Args:
        *filters: DeviceFilter instances to combine with OR logic.

    Example:
        # Select either motors or positioners
        axis: Annotated[Device, DeviceFilterAny(
            DeviceFilter(category="motor"),
            DeviceFilter(category="positioner"),
        )]
    """

    filters: tuple[DeviceFilter, ...] = field(default_factory=tuple)

    def __init__(self, *filters: DeviceFilter) -> None:
        """Initialize with variable number of filters.

        Args:
            *filters: DeviceFilter instances to combine.
        """
        object.__setattr__(self, "filters", filters)


@dataclass(frozen=True)
class DeviceDefault:
    """Default device selection by name or pattern.

    Pre-selects devices in the device selector based on explicit names
    or a regex pattern match.

    Args:
        *names: Device names to pre-select.
        pattern: Regex pattern to match device names for pre-selection.

    Example:
        # Pre-select specific detector
        detector: Annotated[list[Detector], DeviceDefault("PI_MTE3")]

        # Pre-select all devices matching pattern
        motors: Annotated[list[Motor], DeviceDefault(pattern="sample_.*")]
    """

    names: tuple[str, ...] = field(default_factory=tuple)
    pattern: str | None = None

    def __init__(self, *names: str, pattern: str | None = None) -> None:
        """Initialize with device names and/or pattern.

        Args:
            *names: Device names to pre-select.
            pattern: Regex pattern for matching device names.
        """
        object.__setattr__(self, "names", names)
        object.__setattr__(self, "pattern", pattern)


@dataclass(frozen=True)
class DeviceIcon:
    """QtAwesome icon identifier for the device parameter button.

    Specifies which icon to show on the device selector button in the
    plan configuration UI. If the icon string has no dot prefix,
    ``mdi6.`` is prepended automatically.

    Args:
        name: QtAwesome icon identifier (e.g., ``"mdi6.engine"``, ``"camera"``).

    Example:
        motor: Annotated[Device, DeviceFilter(category="motor"), DeviceIcon("engine")]
    """

    name: str


# Type alias for convenience
__all__ = [
    "Unit",
    "Decimals",
    "Range",
    "Default",
    "DeviceFilter",
    "DeviceFilterAny",
    "DeviceDefault",
    "DeviceIcon",
]
