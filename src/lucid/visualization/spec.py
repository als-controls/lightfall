"""Data characteristics and visualization specification.

This module defines the core data structures that describe:
- The characteristics of incoming Bluesky data
- The specification for creating a visualization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


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


@dataclass
class DataCharacteristics:
    """Characteristics of data extracted from Bluesky documents.

    This dataclass holds all information needed by the SelectionEngine
    to determine the best visualization for the data.

    Attributes:
        ndim: Number of independent dimensions (1 for scans, 2 for grids).
        dim_fields: Field names for independent variables (e.g., ["motor1"]).
        dep_fields: Field names for dependent variables (e.g., ["det_image"]).
        num_points: Expected number of points (from start doc).
        shape: Expected shape tuple for gridded data.
        extents: Physical extents as ((x_min, x_max), (y_min, y_max), ...).
        is_rectilinear: Whether data lies on a rectilinear grid.
        gridding: Gridding hint from start doc ("rectilinear" or None).
        field_info: Detailed info for each field.
        plan_name: Name of the Bluesky plan.
        run_uid: UID of the current run.
        metadata: Additional metadata from start document.
    """

    # Dimensionality
    ndim: int = 1
    dim_fields: list[str] = field(default_factory=list)
    dep_fields: list[str] = field(default_factory=list)

    # Shape information
    num_points: int | None = None
    shape: tuple[int, ...] = ()
    extents: tuple[tuple[float, float], ...] = ()

    # Gridding
    is_rectilinear: bool = False
    gridding: str | None = None

    # Field details
    field_info: dict[str, FieldInfo] = field(default_factory=dict)

    # Metadata
    plan_name: str = ""
    run_uid: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_dim_fields(self) -> bool:
        """Check if independent variables are defined."""
        return len(self.dim_fields) > 0

    @property
    def has_dep_fields(self) -> bool:
        """Check if dependent variables are defined."""
        return len(self.dep_fields) > 0

    @property
    def primary_dep_field(self) -> str | None:
        """Get the primary (first) dependent field."""
        return self.dep_fields[0] if self.dep_fields else None

    @property
    def primary_dim_field(self) -> str | None:
        """Get the primary (first) independent field."""
        return self.dim_fields[0] if self.dim_fields else None

    def get_dep_field_type(self) -> FieldType:
        """Get the field type of the primary dependent variable.

        Returns:
            FieldType of the first dependent field, or UNKNOWN.
        """
        if not self.dep_fields:
            return FieldType.UNKNOWN

        primary = self.dep_fields[0]
        if primary in self.field_info:
            return self.field_info[primary].field_type

        return FieldType.UNKNOWN

    def get_field(self, name: str) -> FieldInfo | None:
        """Get FieldInfo for a named field.

        Args:
            name: Field name.

        Returns:
            FieldInfo or None if not found.
        """
        return self.field_info.get(name)

    def get_scalar_fields(self) -> list[str]:
        """Get names of all scalar fields.

        Returns:
            List of field names that are scalar.
        """
        return [
            name for name, info in self.field_info.items() if info.is_scalar
        ]

    def get_array_fields(self, ndim: int | None = None) -> list[str]:
        """Get names of array fields, optionally filtered by dimensionality.

        Args:
            ndim: If specified, only return arrays with this many dimensions.

        Returns:
            List of field names.
        """
        result = []
        for name, info in self.field_info.items():
            if info.is_scalar:
                continue
            if ndim is None or len(info.shape) == ndim:
                result.append(name)
        return result


@dataclass
class VisualizationSpec:
    """Specification for creating a visualization widget.

    This dataclass encapsulates all the information needed by a
    VisualizationPlugin to create and configure a widget.

    Attributes:
        viz_type: The type of visualization to create.
        characteristics: Data characteristics from the document stream.
        x_field: Field to use for X axis (for plots).
        y_field: Field to use for Y axis (for plots).
        z_field: Field to use for Z/color axis (for heatmaps).
        image_field: Field containing image data.
        title: Title for the visualization.
        auto_range: Whether to auto-range axes.
        colormap: Colormap name for image visualizations.
        show_legend: Whether to show legend.
        decimation_threshold: Point count above which to decimate.
        user_preferences: Additional user preferences.
    """

    viz_type: VizType
    characteristics: DataCharacteristics

    # Axis assignments
    x_field: str | None = None
    y_field: str | None = None
    z_field: str | None = None
    image_field: str | None = None

    # Display options
    title: str = ""
    auto_range: bool = True
    colormap: str = "viridis"
    show_legend: bool = True

    # Performance
    decimation_threshold: int = 10000

    # Extensibility
    user_preferences: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def for_plot_1d(
        cls,
        characteristics: DataCharacteristics,
        x_field: str | None = None,
        y_field: str | None = None,
        **kwargs: Any,
    ) -> VisualizationSpec:
        """Create spec for a 1D plot.

        Args:
            characteristics: Data characteristics.
            x_field: X axis field (defaults to primary dim field).
            y_field: Y axis field (defaults to primary dep field).
            **kwargs: Additional spec options.

        Returns:
            VisualizationSpec configured for PLOT_1D.
        """
        return cls(
            viz_type=VizType.PLOT_1D,
            characteristics=characteristics,
            x_field=x_field or characteristics.primary_dim_field,
            y_field=y_field or characteristics.primary_dep_field,
            **kwargs,
        )

    @classmethod
    def for_heatmap(
        cls,
        characteristics: DataCharacteristics,
        x_field: str | None = None,
        y_field: str | None = None,
        z_field: str | None = None,
        **kwargs: Any,
    ) -> VisualizationSpec:
        """Create spec for a heatmap.

        Args:
            characteristics: Data characteristics.
            x_field: X axis field.
            y_field: Y axis field.
            z_field: Color/intensity field.
            **kwargs: Additional spec options.

        Returns:
            VisualizationSpec configured for HEATMAP.
        """
        dim_fields = characteristics.dim_fields
        return cls(
            viz_type=VizType.HEATMAP,
            characteristics=characteristics,
            x_field=x_field or (dim_fields[0] if dim_fields else None),
            y_field=y_field or (dim_fields[1] if len(dim_fields) > 1 else None),
            z_field=z_field or characteristics.primary_dep_field,
            **kwargs,
        )

    @classmethod
    def for_image_stack(
        cls,
        characteristics: DataCharacteristics,
        image_field: str | None = None,
        **kwargs: Any,
    ) -> VisualizationSpec:
        """Create spec for an image stack viewer.

        Args:
            characteristics: Data characteristics.
            image_field: Field containing image arrays.
            **kwargs: Additional spec options.

        Returns:
            VisualizationSpec configured for IMAGE_STACK.
        """
        # Find first 2D array field if not specified
        if image_field is None:
            array_2d = characteristics.get_array_fields(ndim=2)
            image_field = array_2d[0] if array_2d else None

        return cls(
            viz_type=VizType.IMAGE_STACK,
            characteristics=characteristics,
            image_field=image_field,
            **kwargs,
        )

    @classmethod
    def for_table(
        cls, characteristics: DataCharacteristics, **kwargs: Any
    ) -> VisualizationSpec:
        """Create spec for a table view.

        Args:
            characteristics: Data characteristics.
            **kwargs: Additional spec options.

        Returns:
            VisualizationSpec configured for TABLE.
        """
        return cls(
            viz_type=VizType.TABLE,
            characteristics=characteristics,
            **kwargs,
        )
