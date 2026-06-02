"""Visualization type and field classification enums.

Keeps FieldType, FieldInfo, and VizType for use by widgets and heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class VizType(Enum):
    """Enumeration of supported visualization types."""

    TABLE = auto()  # Tabular display of all fields
    PLOT_1D = auto()  # Line plot for 1D scans
    HEATMAP = auto()  # 2D color map for rectilinear grids
    SCATTER = auto()  # Scatter plot for irregular 2D data
    IMAGE_STACK = auto()  # Image sequence viewer
    VOLUME = auto()  # 3D volume with slice navigation


class FieldType(Enum):
    """Type classification for data fields."""

    SCALAR = auto()  # Single numeric value
    ARRAY_1D = auto()  # 1D array (e.g., spectrum)
    ARRAY_2D = auto()  # 2D array (e.g., image)
    ARRAY_3D = auto()  # 3D array (e.g., volume)
    STRING = auto()  # Text data
    UNKNOWN = auto()  # Unclassified


@dataclass
class FieldInfo:
    """Information about a single data field.

    Attributes:
        name: Field name from the descriptor.
        dtype: Data type string (e.g., "number", "integer", "array").
        shape: Shape tuple from descriptor data_keys.
        units: Physical units if available.
        source: Source of the data (device name).
        is_hinted: Whether this field is in the hints.
    """

    name: str
    dtype: str = "number"
    shape: tuple[int, ...] = ()
    units: str = ""
    source: str = ""
    is_hinted: bool = False

    @property
    def field_type(self) -> FieldType:
        """Classify the field based on shape and dtype."""
        if self.dtype == "string":
            return FieldType.STRING

        ndim = len(self.shape)
        if ndim == 0:
            return FieldType.SCALAR
        elif ndim == 1:
            return FieldType.ARRAY_1D
        elif ndim == 2:
            return FieldType.ARRAY_2D
        elif ndim >= 3:
            return FieldType.ARRAY_3D
        return FieldType.UNKNOWN

    @property
    def is_scalar(self) -> bool:
        """Check if field is scalar (single value per event)."""
        return len(self.shape) == 0

    @property
    def is_numeric(self) -> bool:
        """Check if field is numeric."""
        return self.dtype in ("number", "integer", "array")
