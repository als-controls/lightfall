"""Heatmap visualization for 2D rectilinear data.

Provides a color-coded 2D image view with colorbar and crosshairs.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger
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
from lucid.visualization.spec import (
    DataCharacteristics,
    FieldType,
    VisualizationSpec,
)
from lucid.visualization.theme import (
    ThemedVisualizationMixin,
    VisualizationColors,
)

if TYPE_CHECKING:
    import pyqtgraph as pg

    from lucid.acquire.buffer import MultiStreamBuffer


class HeatmapVisualization(ThemedVisualizationMixin, BaseVisualizationWidget):
    """Heatmap visualization for 2D grid data.

    Displays scalar data on a 2D rectilinear grid as a color map.
    Features:
    - Colorbar with adjustable range
    - Multiple colormap options
    - Crosshair showing position and value
    - Auto or manual color scaling
    """

    def __init__(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the heatmap visualization."""
        self._plot_widget: pg.PlotWidget | None = None
        self._image_item: pg.ImageItem | None = None
        self._colorbar: pg.ColorBarItem | None = None
        self._crosshair_v: pg.InfiniteLine | None = None
        self._crosshair_h: pg.InfiniteLine | None = None

        # Data storage
        self._data_grid: np.ndarray | None = None
        self._shape: tuple[int, int] = (0, 0)
        self._current_point = 0

        super().__init__(spec, buffer, parent)
        self._setup_theme()

    def _setup_ui(self) -> None:
        """Setup the heatmap UI."""
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

            # Lock aspect ratio for image data
            plot_item.setAspectLocked(True)

        # Create image item
        self._image_item = pg.ImageItem()
        self._plot_widget.addItem(self._image_item)

        # Apply colormap
        self._apply_colormap(self._spec.colormap)

        # Create colorbar
        try:
            self._colorbar = pg.ColorBarItem(
                values=(0, 1),
                colorMap=pg.colormap.get(self._spec.colormap),
            )
            self._colorbar.setImageItem(self._image_item)
        except Exception as e:
            logger.debug("Could not create colorbar: {}", e)

        # Create crosshairs
        self._crosshair_v = pg.InfiniteLine(angle=90, movable=False)
        self._crosshair_h = pg.InfiniteLine(angle=0, movable=False)
        self._plot_widget.addItem(self._crosshair_v, ignoreBounds=True)
        self._plot_widget.addItem(self._crosshair_h, ignoreBounds=True)
        self._crosshair_v.hide()
        self._crosshair_h.hide()

        # Connect mouse movement
        self._plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

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

        # Z-field selector
        z_label = QLabel("Data:")
        self._z_combo = QComboBox()
        self._z_combo.setMinimumWidth(100)
        self._z_combo.currentTextChanged.connect(self._on_z_field_changed)
        toolbar.addWidget(z_label)
        toolbar.addWidget(self._z_combo)

        # Colormap selector
        cmap_label = QLabel("Colormap:")
        self._cmap_combo = QComboBox()
        self._cmap_combo.addItems([
            "viridis", "plasma", "inferno", "magma", "cividis",
            "gray", "hot", "cool", "jet"
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

    def _on_z_field_changed(self, field: str) -> None:
        """Handle Z field change."""
        if field:
            self._spec.z_field = field
            self._refresh_from_buffer()

    def _on_colormap_changed(self, cmap_name: str) -> None:
        """Handle colormap change."""
        self._spec.colormap = cmap_name
        self._apply_colormap(cmap_name)

    def _apply_colormap(self, cmap_name: str) -> None:
        """Apply colormap to image item."""
        try:
            import pyqtgraph as pg

            cmap = pg.colormap.get(cmap_name)
            if cmap and self._image_item:
                lut = cmap.getLookupTable(nPts=256)
                self._image_item.setLookupTable(lut)
        except Exception as e:
            logger.debug("Could not apply colormap '{}': {}", cmap_name, e)

    def _on_auto_range(self) -> None:
        """Reset to auto range."""
        if self._plot_widget:
            self._plot_widget.autoRange()

    def _on_mouse_moved(self, pos) -> None:
        """Handle mouse movement for crosshairs."""
        if not self._plot_widget or not self._data_grid is not None:
            return

        if self._plot_widget.sceneBoundingRect().contains(pos):
            mouse_point = self._plot_widget.getPlotItem().vb.mapSceneToView(pos)
            x, y = mouse_point.x(), mouse_point.y()

            # Update crosshairs
            self._crosshair_v.setPos(x)
            self._crosshair_h.setPos(y)
            self._crosshair_v.show()
            self._crosshair_h.show()

            # Update status with value
            if self._data_grid is not None:
                ix = int(x)
                iy = int(y)
                if 0 <= ix < self._data_grid.shape[0] and 0 <= iy < self._data_grid.shape[1]:
                    val = self._data_grid[ix, iy]
                    self._status_label.setText(f"x={x:.2f}, y={y:.2f}, value={val:.4g}")

    def _initialize_grid(self, shape: tuple[int, int]) -> None:
        """Initialize the data grid.

        Args:
            shape: (nx, ny) shape of the grid.
        """
        self._shape = shape
        self._data_grid = np.full(shape, np.nan, dtype=np.float64)
        self._current_point = 0
        logger.debug("Initialized heatmap grid: {}", shape)

    def _on_new_point(self, seq_num: int, data: dict[str, Any]) -> None:
        """Handle new data point."""
        z_field = self._spec.z_field

        if not z_field:
            # Try to find z_field from data
            scalar_fields = [k for k, v in data.items()
                           if not hasattr(v, "shape") or not v.shape]
            if scalar_fields:
                z_field = scalar_fields[0]
                self._spec.z_field = z_field

        if not z_field or z_field not in data:
            return

        # Initialize grid if needed
        if self._data_grid is None:
            # Try to get shape from characteristics
            shape = self._spec.characteristics.shape
            if len(shape) >= 2:
                self._initialize_grid((shape[0], shape[1]))
            else:
                # Default to reasonable size
                num_points = self._spec.characteristics.num_points or 100
                side = int(np.sqrt(num_points))
                self._initialize_grid((side, side))

        # Get the value
        value = data[z_field]
        if hasattr(value, "shape") and value.shape:
            # Array data - take mean or first value
            value = float(np.mean(value))
        else:
            value = float(value)

        # Place in grid (row-major order)
        ny = self._shape[1]
        ix = self._current_point // ny
        iy = self._current_point % ny

        if ix < self._shape[0] and iy < self._shape[1]:
            self._data_grid[ix, iy] = value
            self._current_point += 1

        # Update display
        self._update_image()

        # Update field selectors on first point
        if seq_num == 1:
            self._update_field_selectors(data)

    def _update_image(self) -> None:
        """Update the image display."""
        if self._image_item and self._data_grid is not None:
            self._image_item.setImage(self._data_grid.T)  # Transpose for display

    def _update_field_selectors(self, data: dict[str, Any]) -> None:
        """Update field combo boxes."""
        scalar_fields = [k for k, v in data.items()
                        if not hasattr(v, "shape") or not v.shape]

        self._z_combo.blockSignals(True)
        self._z_combo.clear()
        self._z_combo.addItems(scalar_fields)
        if self._spec.z_field in scalar_fields:
            self._z_combo.setCurrentText(self._spec.z_field)
        self._z_combo.blockSignals(False)

    def _refresh_from_buffer(self) -> None:
        """Refresh display from buffer."""
        # Would re-read all data from buffer and rebuild grid
        pass

    def _on_clear(self) -> None:
        """Handle clear request."""
        self._data_grid = None
        self._current_point = 0
        if self._image_item:
            self._image_item.clear()

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

        if self._crosshair_v:
            self._crosshair_v.setPen(pg.mkPen(color=colors.highlight, width=1))
        if self._crosshair_h:
            self._crosshair_h.setPen(pg.mkPen(color=colors.highlight, width=1))

    def _export_data(self, format: str) -> bytes:
        """Export heatmap data."""
        if self._data_grid is None:
            return b""

        if format == "csv":
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            for row in self._data_grid:
                writer.writerow(row)
            return output.getvalue().encode("utf-8")

        elif format == "json":
            data = {
                "z_field": self._spec.z_field,
                "shape": self._shape,
                "data": self._data_grid.tolist(),
            }
            return json.dumps(data, indent=2).encode("utf-8")

        else:
            raise ValueError(f"Unsupported export format: {format}")

    def get_supported_export_formats(self) -> list[str]:
        return ["csv", "json"]


class HeatmapVisualizationPlugin(VisualizationPlugin):
    """Plugin for heatmap visualization."""

    @property
    def name(self) -> str:
        return "heatmap"

    @property
    def display_name(self) -> str:
        return "Heatmap"

    @property
    def icon(self) -> str:
        return "grid"

    @property
    def description(self) -> str:
        return "2D color map for rectilinear grid data"

    def can_handle(self, characteristics: DataCharacteristics) -> int:
        """Check if data is suitable for heatmap.

        Best for:
        - 2D scans (two independent variables)
        - Rectilinear grid
        - Scalar dependent variable
        """
        # Must be 2D
        if characteristics.ndim != 2:
            return 0

        # Check for rectilinear gridding
        if not characteristics.is_rectilinear:
            return 30  # Can still show, but not ideal

        # Check for scalar dependent variable
        dep_type = characteristics.get_dep_field_type()
        if dep_type == FieldType.SCALAR:
            return 85  # Excellent match
        elif dep_type == FieldType.UNKNOWN:
            return 60  # May work
        else:
            return 20  # Array data not ideal

    def create_widget(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> HeatmapVisualization:
        return HeatmapVisualization(spec, buffer, parent)

    def get_default_spec(
        self, characteristics: DataCharacteristics
    ) -> VisualizationSpec:
        return VisualizationSpec.for_heatmap(characteristics)
