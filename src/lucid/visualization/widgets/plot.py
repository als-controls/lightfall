"""1D Plot visualization for Bluesky data.

Provides line plots with real-time updates, optional curve fitting,
and data decimation for large datasets.
"""

from __future__ import annotations

import csv
import io
import json
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import Signal
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
from lucid.visualization.memory import StreamingDecimator
from lucid.visualization.motor_mixin import VisualizationMotorMixin
from lucid.visualization.spec import (
    DataCharacteristics,
    FieldType,
    VisualizationSpec,
)
from lucid.visualization.theme import ThemedVisualizationMixin, VisualizationColors

if TYPE_CHECKING:
    import pyqtgraph as pg

    from lucid.acquire.buffer import MultiStreamBuffer


class PlotVisualization(
    VisualizationMotorMixin, ThemedVisualizationMixin, BaseVisualizationWidget
):
    """1D Plot visualization widget.

    Displays one or more traces on a line plot with:
    - Automatic decimation for large datasets
    - Optional curve fitting overlay
    - Configurable axes
    - Theme-aware styling
    - Right-click context menu for motor movement (Go to X)

    Signals:
        fit_requested: Emitted when user requests a fit.
    """

    fit_requested = Signal()

    def __init__(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the plot visualization."""
        self._plot_widget: pg.PlotWidget | None = None
        self._plot_item: pg.PlotDataItem | None = None
        self._fit_item: pg.PlotDataItem | None = None

        # Data management
        self._decimator: StreamingDecimator | None = None
        self._x_data: list[float] = []
        self._y_data: list[float] = []

        # Multiple trace support
        self._traces: dict[str, pg.PlotDataItem] = {}
        self._trace_decimators: dict[str, StreamingDecimator] = {}

        super().__init__(spec, buffer, parent)
        self._setup_theme()

    def _setup_ui(self) -> None:
        """Setup the plot UI."""
        import pyqtgraph as pg

        # Create main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Create toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)

        # Create plot widget
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground(None)  # Use stylesheet

        # Configure plot
        plot_item = self._plot_widget.getPlotItem()
        if plot_item:
            plot_item.setLabel("bottom", self._spec.x_field or "x")
            plot_item.setLabel("left", self._spec.y_field or "y")

            if self._spec.title:
                plot_item.setTitle(self._spec.title)

            # Enable grid
            plot_item.showGrid(x=True, y=True, alpha=0.3)

            # Enable legend
            if self._spec.show_legend:
                plot_item.addLegend()

        # Create main data trace
        self._plot_item = self._plot_widget.plot(
            pen=pg.mkPen(color="#3b82f6", width=2),
            name=self._spec.y_field or "data",
        )

        # Create fit overlay (initially hidden)
        self._fit_item = self._plot_widget.plot(
            pen=pg.mkPen(color="#ef4444", width=2, style=pg.QtCore.Qt.PenStyle.DashLine),
            name="fit",
        )
        self._fit_item.hide()

        # Initialize decimator
        threshold = self._spec.decimation_threshold
        self._decimator = StreamingDecimator(max_display_points=threshold)

        main_layout.addWidget(self._plot_widget)

        # Setup motor movement context menu (from VisualizationMotorMixin)
        self._setup_motor_context_menu()

        # Replace the default layout
        # Clear existing layout items
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Add our layout
        container = QWidget()
        container.setLayout(main_layout)
        self._layout.addWidget(container)

    def _create_toolbar(self) -> QHBoxLayout:
        """Create the toolbar with controls."""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # X-axis selector
        x_label = QLabel("X:")
        self._x_combo = QComboBox()
        self._x_combo.setMinimumWidth(100)
        self._x_combo.currentTextChanged.connect(self._on_x_field_changed)
        toolbar.addWidget(x_label)
        toolbar.addWidget(self._x_combo)

        # Y-axis selector
        y_label = QLabel("Y:")
        self._y_combo = QComboBox()
        self._y_combo.setMinimumWidth(100)
        self._y_combo.currentTextChanged.connect(self._on_y_field_changed)
        toolbar.addWidget(y_label)
        toolbar.addWidget(self._y_combo)

        toolbar.addStretch()

        # Auto-range button
        auto_btn = QPushButton("Auto Range")
        auto_btn.clicked.connect(self._on_auto_range)
        toolbar.addWidget(auto_btn)

        # Fit button (will be connected to fitting panel)
        fit_btn = QPushButton("Fit...")
        fit_btn.clicked.connect(self.fit_requested.emit)
        toolbar.addWidget(fit_btn)

        return toolbar

    def _on_x_field_changed(self, field: str) -> None:
        """Handle X field selection change."""
        if not field:
            return
        self._spec.x_field = field
        self._refresh_plot()

    def _on_y_field_changed(self, field: str) -> None:
        """Handle Y field selection change."""
        if not field:
            return
        self._spec.y_field = field
        self._refresh_plot()

    def _on_auto_range(self) -> None:
        """Reset view to auto range."""
        if self._plot_widget:
            self._plot_widget.autoRange()

    def _refresh_plot(self) -> None:
        """Refresh plot with current data from buffer."""
        stream = self.stream_buffer
        if not stream or not self._spec.x_field or not self._spec.y_field:
            return

        x_data = stream.get_array(self._spec.x_field)
        y_data = stream.get_array(self._spec.y_field)

        if x_data is None or y_data is None:
            return

        # Reinitialize decimator with new data
        if self._decimator:
            self._decimator.clear()
            if len(x_data) > 0:
                self._decimator.add_points(x_data, y_data)
                display_x, display_y = self._decimator.get_display_data()
                if self._plot_item:
                    self._plot_item.setData(display_x, display_y)

    def _on_new_point(self, seq_num: int, data: dict[str, Any]) -> None:
        """Handle new data point."""
        x_field = self._spec.x_field
        y_field = self._spec.y_field

        if not x_field or not y_field:
            return

        x_val = data.get(x_field)
        y_val = data.get(y_field)

        if x_val is None or y_val is None:
            return

        # Handle scalar values only for 1D plots
        if hasattr(x_val, "shape") and x_val.shape:
            return  # Array data, not suitable for simple plot
        if hasattr(y_val, "shape") and y_val.shape:
            return  # Array data

        # Store raw data
        self._x_data.append(float(x_val))
        self._y_data.append(float(y_val))

        # Add to decimator and update plot
        if self._decimator:
            self._decimator.add_point(float(x_val), float(y_val))
            display_x, display_y = self._decimator.get_display_data()

            if self._plot_item:
                self._plot_item.setData(display_x, display_y)

        # Update field selectors on first point
        if seq_num == 1:
            self._update_field_selectors(data)

    def _update_field_selectors(self, data: dict[str, Any]) -> None:
        """Update field combo boxes with available fields."""
        # Get scalar fields
        scalar_fields = []
        for key, val in data.items():
            if not hasattr(val, "shape") or not val.shape:
                scalar_fields.append(key)

        # Block signals during update
        self._x_combo.blockSignals(True)
        self._y_combo.blockSignals(True)

        self._x_combo.clear()
        self._y_combo.clear()

        self._x_combo.addItems(scalar_fields)
        self._y_combo.addItems(scalar_fields)

        # Set current selections
        if self._spec.x_field in scalar_fields:
            self._x_combo.setCurrentText(self._spec.x_field)
        if self._spec.y_field in scalar_fields:
            self._y_combo.setCurrentText(self._spec.y_field)

        self._x_combo.blockSignals(False)
        self._y_combo.blockSignals(False)

    def _on_clear(self) -> None:
        """Handle clear request."""
        self._x_data.clear()
        self._y_data.clear()

        if self._decimator:
            self._decimator.clear()

        if self._plot_item:
            self._plot_item.setData([], [])

        if self._fit_item:
            self._fit_item.setData([], [])
            self._fit_item.hide()

    def _apply_viz_colors(self, colors: VisualizationColors) -> None:
        """Apply visualization colors to plot."""
        import pyqtgraph as pg

        if self._plot_widget:
            self._plot_widget.setBackground(colors.background)

            plot_item = self._plot_widget.getPlotItem()
            if plot_item:
                # Axis colors
                for axis_name in ["bottom", "left", "top", "right"]:
                    axis = plot_item.getAxis(axis_name)
                    if axis:
                        axis.setPen(pg.mkPen(color=colors.foreground))
                        axis.setTextPen(pg.mkPen(color=colors.foreground))

            # Update data line color
            if self._plot_item:
                self._plot_item.setPen(pg.mkPen(color=colors.primary_line, width=2))

            # Update fit line color
            if self._fit_item:
                self._fit_item.setPen(
                    pg.mkPen(
                        color=colors.fit_line,
                        width=2,
                        style=pg.QtCore.Qt.PenStyle.DashLine,
                    )
                )

    def set_fit_data(self, x: np.ndarray, y: np.ndarray) -> None:
        """Set the fit curve overlay data.

        Args:
            x: X values for fit curve.
            y: Y values for fit curve.
        """
        if self._fit_item:
            self._fit_item.setData(x, y)
            self._fit_item.show()

    def clear_fit(self) -> None:
        """Clear the fit curve overlay."""
        if self._fit_item:
            self._fit_item.setData([], [])
            self._fit_item.hide()

    def get_data_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        """Get the full data arrays.

        Returns:
            Tuple of (x, y) numpy arrays.
        """
        return np.array(self._x_data), np.array(self._y_data)

    def _export_data(self, format: str) -> bytes:
        """Export plot data."""
        x_data = self._x_data
        y_data = self._y_data

        if format == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([self._spec.x_field or "x", self._spec.y_field or "y"])
            for x, y in zip(x_data, y_data, strict=False):
                writer.writerow([x, y])
            return output.getvalue().encode("utf-8")

        elif format == "json":
            data = {
                "x_field": self._spec.x_field,
                "y_field": self._spec.y_field,
                "x": x_data,
                "y": y_data,
            }
            return json.dumps(data, indent=2).encode("utf-8")

        elif format == "png":
            # Export plot as image
            if self._plot_widget:
                import pyqtgraph.exporters as exporters

                exporter = exporters.ImageExporter(self._plot_widget.plotItem)
                exporter.parameters()["width"] = 1920
                # Return as bytes - would need temp file in practice
                # For now, raise not implemented
                raise ValueError("PNG export requires file path")

        else:
            raise ValueError(f"Unsupported export format: {format}")

    def get_supported_export_formats(self) -> list[str]:
        """Get supported export formats."""
        return ["csv", "json"]


class PlotVisualizationPlugin(VisualizationPlugin):
    """Plugin for 1D plot visualization."""

    @property
    def name(self) -> str:
        return "plot_1d"

    @property
    def display_name(self) -> str:
        return "1D Plot"

    @property
    def icon(self) -> str:
        return "chart-line"

    @property
    def description(self) -> str:
        return "Line plot for 1D scans with optional curve fitting"

    def can_handle(self, characteristics: DataCharacteristics) -> int:
        """Determine if data is suitable for 1D plot.

        Best for:
        - 1D scans (single independent variable)
        - Scalar dependent variables

        Returns:
            80 for optimal match, lower for suboptimal.
        """
        # Check for 1D scan
        if characteristics.ndim != 1:
            return 0

        # Check for scalar dependent variables
        dep_type = characteristics.get_dep_field_type()
        if dep_type == FieldType.SCALAR:
            return 80  # Excellent match

        if dep_type == FieldType.UNKNOWN:
            # May still work, return moderate score
            return 50

        # Array data not ideal for simple plot
        return 20

    def create_widget(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> PlotVisualization:
        """Create the plot widget."""
        return PlotVisualization(spec, buffer, parent)

    def get_default_spec(
        self, characteristics: DataCharacteristics
    ) -> VisualizationSpec:
        """Get default spec for 1D plot."""
        return VisualizationSpec.for_plot_1d(characteristics)
