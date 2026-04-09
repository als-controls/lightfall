"""Scatter plot visualization for irregular 2D data.

Provides a scatter plot view for non-rectilinear 2D data with
color-coded values.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.visualization_plugin import VisualizationPlugin
from lucid.visualization.base import BaseVisualizationWidget
from lucid.visualization.motor_mixin import VisualizationMotorMixin
from lucid.visualization.spec import (
    DataCharacteristics,
    FieldType,
    VisualizationSpec,
    VizType,
)
from lucid.visualization.theme import ThemedVisualizationMixin, VisualizationColors

if TYPE_CHECKING:
    import pyqtgraph as pg

    from lucid.acquire.buffer import MultiStreamBuffer


class ScatterVisualization(
    VisualizationMotorMixin, ThemedVisualizationMixin, BaseVisualizationWidget
):
    """Scatter plot visualization for irregular 2D data.

    Displays data points as colored circles at their (x, y) positions
    with color indicating the value (z).

    Features:
    - Color-coded scatter points
    - Adjustable point size
    - Multiple colormap options
    - Hover tooltips showing values
    - Right-click context menu for motor movement (Go to X, Y, or X,Y)
    """

    def __init__(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the scatter visualization."""
        self._plot_widget: pg.PlotWidget | None = None
        self._scatter_item: pg.ScatterPlotItem | None = None

        # Data storage
        self._x_data: list[float] = []
        self._y_data: list[float] = []
        self._z_data: list[float] = []
        self._field_arrays: dict[str, np.ndarray] | None = None

        self._point_size = 10

        super().__init__(spec, buffer, parent)
        self._setup_theme()

    def _setup_ui(self) -> None:
        """Setup the scatter plot UI."""
        import pyqtgraph as pg

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)

        # Create plot widget
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground(None)

        # Configure axes
        plot_item = self._plot_widget.getPlotItem()
        if plot_item:
            x_label = self._spec.x_field or "x"
            y_label = self._spec.y_field or "y"
            plot_item.setLabel("bottom", x_label)
            plot_item.setLabel("left", y_label)

            if self._spec.title:
                plot_item.setTitle(self._spec.title)

            # Enable grid
            plot_item.showGrid(x=True, y=True, alpha=0.3)

        # Create scatter item
        self._scatter_item = pg.ScatterPlotItem(
            size=self._point_size,
            pen=pg.mkPen(None),
            brush=pg.mkBrush(100, 100, 255, 200),
        )
        self._plot_widget.addItem(self._scatter_item)

        # Setup motor movement context menu (from VisualizationMotorMixin)
        self._setup_motor_context_menu()

        main_layout.addWidget(self._plot_widget)

        # Status bar
        self._status_label = QLabel("")
        main_layout.addWidget(self._status_label)

        # Add to parent layout
        container = QWidget()
        container.setLayout(main_layout)
        self._layout.addWidget(container)

    def _create_toolbar(self) -> QHBoxLayout:
        """Create the toolbar."""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # X-field selector
        x_label = QLabel("X:")
        self._x_combo = QComboBox()
        self._x_combo.setMinimumWidth(80)
        self._x_combo.currentTextChanged.connect(self._on_field_changed)
        toolbar.addWidget(x_label)
        toolbar.addWidget(self._x_combo)

        # Y-field selector
        y_label = QLabel("Y:")
        self._y_combo = QComboBox()
        self._y_combo.setMinimumWidth(80)
        self._y_combo.currentTextChanged.connect(self._on_field_changed)
        toolbar.addWidget(y_label)
        toolbar.addWidget(self._y_combo)

        # Z-field selector (color)
        z_label = QLabel("Color:")
        self._z_combo = QComboBox()
        self._z_combo.setMinimumWidth(80)
        self._z_combo.currentTextChanged.connect(self._on_field_changed)
        toolbar.addWidget(z_label)
        toolbar.addWidget(self._z_combo)

        # Colormap selector
        cmap_label = QLabel("Colormap:")
        self._cmap_combo = QComboBox()
        self._cmap_combo.addItems([
            "viridis", "plasma", "inferno", "magma",
            "gray", "hot", "cool"
        ])
        self._cmap_combo.setCurrentText(self._spec.colormap)
        self._cmap_combo.currentTextChanged.connect(self._on_colormap_changed)
        toolbar.addWidget(cmap_label)
        toolbar.addWidget(self._cmap_combo)

        toolbar.addStretch()

        # Auto-range button
        auto_btn = QPushButton("Auto Range")
        auto_btn.clicked.connect(self._on_auto_range)
        toolbar.addWidget(auto_btn)

        return toolbar

    def _on_field_changed(self, _: str) -> None:
        """Handle field selection change."""
        self._spec.x_field = self._x_combo.currentText() or None
        self._spec.y_field = self._y_combo.currentText() or None
        self._spec.z_field = self._z_combo.currentText() or None
        self._refresh_from_buffer()

    def _on_colormap_changed(self, cmap_name: str) -> None:
        """Handle colormap change."""
        self._spec.colormap = cmap_name
        self._update_scatter()

    def _on_auto_range(self) -> None:
        """Reset to auto range."""
        if self._plot_widget:
            self._plot_widget.autoRange()

    # === Tiled bulk-load path ===

    def set_data(
        self,
        field_arrays: dict[str, np.ndarray],
        field_names: list[str],
    ) -> None:
        """Bulk-load scalar data from tiled ArrayClients."""
        self._field_arrays = field_arrays

        for combo in [self._x_combo, self._y_combo, self._z_combo]:
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(field_names)

        if self._spec.x_field in field_names:
            self._x_combo.setCurrentText(self._spec.x_field)
        if self._spec.y_field in field_names:
            self._y_combo.setCurrentText(self._spec.y_field)
        if self._spec.z_field in field_names:
            self._z_combo.setCurrentText(self._spec.z_field)

        for combo in [self._x_combo, self._y_combo, self._z_combo]:
            combo.blockSignals(False)

        self._load_from_field_arrays()

    def _load_from_field_arrays(self) -> None:
        """Reload scatter data from stored field_arrays."""
        if self._field_arrays is None:
            return

        x_field = self._spec.x_field
        y_field = self._spec.y_field
        z_field = self._spec.z_field
        if not x_field or not y_field:
            return

        x_arr = self._field_arrays.get(x_field)
        y_arr = self._field_arrays.get(y_field)
        if x_arr is None or y_arr is None:
            return

        n = min(len(x_arr), len(y_arr))
        self._x_data = [float(v) for v in x_arr[:n]]
        self._y_data = [float(v) for v in y_arr[:n]]

        if z_field and z_field in self._field_arrays:
            z_arr = self._field_arrays[z_field]
            self._z_data = [float(v) for v in z_arr[:n]]
        else:
            self._z_data = [0.0] * n

        self._update_scatter()

    def _on_new_point(self, seq_num: int, data: dict[str, Any]) -> None:
        """Handle new data point."""
        x_field = self._spec.x_field
        y_field = self._spec.y_field
        z_field = self._spec.z_field

        # Try to auto-detect fields on first point
        if seq_num == 1:
            self._update_field_selectors(data)

            # Auto-assign if not set
            scalar_fields = [k for k, v in data.items()
                           if not hasattr(v, "shape") or not v.shape]
            dim_fields = self._spec.characteristics.dim_fields

            if not x_field and len(dim_fields) > 0:
                x_field = dim_fields[0]
                self._spec.x_field = x_field
            if not y_field and len(dim_fields) > 1:
                y_field = dim_fields[1]
                self._spec.y_field = y_field
            if not z_field:
                # Pick first non-dimension scalar field
                for f in scalar_fields:
                    if f not in dim_fields:
                        z_field = f
                        self._spec.z_field = z_field
                        break

        if not x_field or not y_field:
            return

        x_val = data.get(x_field)
        y_val = data.get(y_field)
        z_val = data.get(z_field) if z_field else 0.0

        if x_val is None or y_val is None:
            return

        # Ensure scalar values
        if hasattr(x_val, "shape") and x_val.shape:
            x_val = float(np.mean(x_val))
        if hasattr(y_val, "shape") and y_val.shape:
            y_val = float(np.mean(y_val))
        if z_val is not None and hasattr(z_val, "shape") and z_val.shape:
            z_val = float(np.mean(z_val))

        self._x_data.append(float(x_val))
        self._y_data.append(float(y_val))
        self._z_data.append(float(z_val) if z_val is not None else 0.0)

        self._update_scatter()

    def _update_scatter(self) -> None:
        """Update the scatter plot display."""
        if not self._scatter_item or not self._x_data:
            return

        import pyqtgraph as pg

        x = np.array(self._x_data)
        y = np.array(self._y_data)
        z = np.array(self._z_data)

        # Normalize z values for color mapping
        z_min, z_max = z.min(), z.max()
        if z_max > z_min:
            z_norm = (z - z_min) / (z_max - z_min)
        else:
            z_norm = np.zeros_like(z)

        # Get colormap
        try:
            cmap = pg.colormap.get(self._spec.colormap)
            colors = cmap.map(z_norm, mode="qcolor")
        except Exception:
            # Fallback to simple blue
            colors = [pg.mkBrush(100, 100, 255, 200)] * len(z_norm)

        # Update scatter
        self._scatter_item.setData(
            x=x,
            y=y,
            brush=colors,
            size=self._point_size,
        )

    def _update_field_selectors(self, data: dict[str, Any]) -> None:
        """Update field combo boxes."""
        from loguru import logger

        scalar_fields = [k for k, v in data.items()
                        if not hasattr(v, "shape") or not v.shape]

        logger.debug(
            "Scatter _update_field_selectors: scalar_fields={}, "
            "spec.x_field='{}', spec.y_field='{}', spec.z_field='{}', "
            "dim_fields={}",
            scalar_fields,
            self._spec.x_field,
            self._spec.y_field,
            self._spec.z_field,
            self._spec.characteristics.dim_fields,
        )

        # Block signals on all combos while updating
        for combo in [self._x_combo, self._y_combo, self._z_combo]:
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(scalar_fields)

        # Set current selections while still blocked
        if self._spec.x_field in scalar_fields:
            self._x_combo.setCurrentText(self._spec.x_field)
        if self._spec.y_field in scalar_fields:
            self._y_combo.setCurrentText(self._spec.y_field)
        if self._spec.z_field in scalar_fields:
            self._z_combo.setCurrentText(self._spec.z_field)

        # Now unblock signals
        for combo in [self._x_combo, self._y_combo, self._z_combo]:
            combo.blockSignals(False)

    def _refresh_from_buffer(self) -> None:
        """Refresh from buffer data (or field_arrays)."""
        if self._field_arrays is not None:
            self._load_from_field_arrays()
            return
        # Would rebuild scatter from buffer
        pass

    def _on_clear(self) -> None:
        """Handle clear request."""
        self._x_data.clear()
        self._y_data.clear()
        self._z_data.clear()
        self._field_arrays = None
        if self._scatter_item:
            self._scatter_item.clear()

    def _apply_viz_colors(self, colors: VisualizationColors) -> None:
        """Apply theme colors."""
        import pyqtgraph as pg

        if self._plot_widget:
            self._plot_widget.setBackground(colors.background)

            plot_item = self._plot_widget.getPlotItem()
            if plot_item:
                for axis_name in ["bottom", "left", "top", "right"]:
                    axis = plot_item.getAxis(axis_name)
                    if axis:
                        axis.setPen(pg.mkPen(color=colors.foreground))
                        axis.setTextPen(pg.mkPen(color=colors.foreground))

    def _export_data(self, format: str) -> bytes:
        """Export scatter data."""
        if format == "csv":
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                self._spec.x_field or "x",
                self._spec.y_field or "y",
                self._spec.z_field or "z"
            ])
            for x, y, z in zip(self._x_data, self._y_data, self._z_data, strict=False):
                writer.writerow([x, y, z])
            return output.getvalue().encode("utf-8")

        elif format == "json":
            data = {
                "x_field": self._spec.x_field,
                "y_field": self._spec.y_field,
                "z_field": self._spec.z_field,
                "x": self._x_data,
                "y": self._y_data,
                "z": self._z_data,
            }
            return json.dumps(data, indent=2).encode("utf-8")

        else:
            raise ValueError(f"Unsupported export format: {format}")

    def get_supported_export_formats(self) -> list[str]:
        return ["csv", "json"]


class ScatterVisualizationPlugin(VisualizationPlugin):
    """Plugin for scatter visualization."""

    @property
    def name(self) -> str:
        return "scatter"

    @property
    def display_name(self) -> str:
        return "Scatter Plot"

    @property
    def icon(self) -> str:
        return "scatter-chart"

    @property
    def description(self) -> str:
        return "Scatter plot for irregular 2D data with color-coded values"

    def can_handle(self, characteristics: DataCharacteristics) -> int:
        """Check if data is suitable for scatter plot.

        Best for:
        - 2D scans with non-rectilinear grid
        - Scalar dependent variable
        """
        # Works for 2D data
        if characteristics.ndim != 2:
            return 0

        # Prefer non-rectilinear data
        if characteristics.is_rectilinear:
            return 50  # Can show but heatmap is better

        # Check for scalar dependent variable
        dep_type = characteristics.get_dep_field_type()
        if dep_type == FieldType.SCALAR:
            return 70  # Good match for irregular data
        elif dep_type == FieldType.UNKNOWN:
            return 50
        else:
            return 20

    def create_widget(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> ScatterVisualization:
        return ScatterVisualization(spec, buffer, parent)

    def get_default_spec(
        self, characteristics: DataCharacteristics
    ) -> VisualizationSpec:
        # Similar to heatmap spec
        dim_fields = characteristics.dim_fields
        return VisualizationSpec(
            viz_type=VizType.SCATTER,
            characteristics=characteristics,
            x_field=dim_fields[0] if dim_fields else None,
            y_field=dim_fields[1] if len(dim_fields) > 1 else None,
            z_field=characteristics.primary_dep_field,
        )
