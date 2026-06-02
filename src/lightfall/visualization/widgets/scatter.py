"""Scatter plot visualization on the new BaseVisualization ABC.

Reads scalar data from a tiled BlueskyRun's internal/events table and
plots motor[0] vs motor[1] with the selected field mapped to color.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from lucid.visualization.base_visualization import BaseVisualization


class ScatterVisualization(BaseVisualization):
    """Tiled-backed scatter plot.

    Plots motor[0] vs motor[1] with the selected scalar field
    mapped to a viridis color scale.
    """

    viz_name = "scatter"
    viz_display_name = "Scatter Plot"
    viz_icon = "scatter-chart"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Tiled state
        self._stream: Any | None = None
        self._data_keys: dict[str, Any] = {}
        self._motors: list[str] = []

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        layout.addLayout(self._build_toolbar())

        self._plot_widget = pg.PlotWidget()
        plot_item = self._plot_widget.getPlotItem()
        if plot_item:
            plot_item.showGrid(x=True, y=True, alpha=0.3)

        self._scatter_item = pg.ScatterPlotItem(
            size=8,
            pen=pg.mkPen(None),
        )
        self._plot_widget.addItem(self._scatter_item)

        layout.addWidget(self._plot_widget)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("Color:"))
        self._color_combo = QComboBox()
        self._color_combo.setMinimumWidth(120)
        self._color_combo.currentTextChanged.connect(self._on_color_field_changed)
        toolbar.addWidget(self._color_combo)

        toolbar.addStretch()
        return toolbar

    # ------------------------------------------------------------------
    # BaseVisualization interface
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Return 70 for 2D non-rectilinear, 50 for 2D rectilinear, 0 otherwise."""
        try:
            start = run.metadata.get("start", {})
            hints = start.get("hints", {})
            dims = hints.get("dimensions", [])
        except Exception:
            return 0

        if len(dims) != 2:
            return 0

        # Check if rectilinear (shape present with two valid ints)
        try:
            shape = start.get("shape", [])
            if len(shape) >= 2 and shape[0] > 0 and shape[1] > 0:
                return 50  # Rectilinear — heatmap is better
        except Exception:
            pass

        return 70  # Non-rectilinear — scatter is preferred

    def set_run(self, run: Any) -> None:
        self._run = run

    def get_streams(self) -> list[str]:
        if self._run is None:
            return []
        names = list(self._run.keys())
        if "primary" in names:
            names.remove("primary")
            names.insert(0, "primary")
        return names

    def set_stream(self, stream_name: str) -> None:
        self._stream_name = stream_name
        try:
            self._stream = self._run[stream_name]
            self._data_keys = self._stream.metadata.get("data_keys", {})
        except Exception as e:
            logger.debug("Scatter: could not open stream '{}': {}", stream_name, e)
            self._data_keys = {}

        self._motors = self._detect_motors()

        # Populate color field combo
        fields = self.get_fields()
        self._color_combo.blockSignals(True)
        self._color_combo.clear()
        self._color_combo.addItems(fields)
        self._color_combo.blockSignals(False)

        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        """Return scalar fields, motors excluded."""
        if not self._data_keys:
            return []

        motor_set = set(self._motors)
        scalars: list[str] = []

        for name, dk in self._data_keys.items():
            if name in motor_set:
                continue
            shape = dk.get("shape", [])
            dtype = dk.get("dtype", "")
            is_scalar = shape == [] or shape == ()
            is_numeric = dtype in ("number", "integer", "float", "int", "")
            if is_scalar and is_numeric:
                scalars.append(name)

        return scalars

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self._replot()

    def refresh(self) -> None:
        self._replot()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_motors(self) -> list[str]:
        motors: list[str] = []
        try:
            start = self._run.metadata.get("start", {})
            dims = start.get("hints", {}).get("dimensions", [])
            for fields, _stream in dims:
                motors.extend(fields)
        except Exception:
            pass
        return motors

    def _read_events_table(self):
        from lucid.utils.tiled_helpers import read_events
        return read_events(self._stream)

    def _on_color_field_changed(self, field: str) -> None:
        if field:
            self._field_name = field
            self._replot()

    def _replot(self) -> None:
        if not self._field_name:
            return
        if len(self._motors) < 2:
            logger.debug("Scatter: need at least 2 motors, got {}", self._motors)
            return

        events = self._read_events_table()
        if events is None:
            return

        try:
            x_arr = np.asarray(events[self._motors[0]], dtype=np.float64)
            y_arr = np.asarray(events[self._motors[1]], dtype=np.float64)
        except Exception as e:
            logger.debug("Scatter: could not read motor fields: {}", e)
            return

        try:
            z_arr = np.asarray(events[self._field_name], dtype=np.float64)
        except Exception as e:
            logger.debug("Scatter: field '{}' not in events: {}", self._field_name, e)
            z_arr = np.zeros(len(x_arr), dtype=np.float64)

        n = min(len(x_arr), len(y_arr), len(z_arr))
        x_arr = x_arr[:n]
        y_arr = y_arr[:n]
        z_arr = z_arr[:n]

        # Normalize z for colormap
        z_min, z_max = float(np.nanmin(z_arr)), float(np.nanmax(z_arr))
        if z_max > z_min:
            z_norm = (z_arr - z_min) / (z_max - z_min)
        else:
            z_norm = np.zeros_like(z_arr)

        # Map to colors via viridis
        try:
            cmap = pg.colormap.get("viridis")
            brushes = [pg.mkBrush(cmap.map(v, mode="qcolor")) for v in z_norm]
        except Exception:
            brushes = [pg.mkBrush(100, 100, 255, 200)] * n

        self._scatter_item.setData(x=x_arr, y=y_arr, brush=brushes)

        plot_item = self._plot_widget.getPlotItem()
        if plot_item:
            plot_item.setLabel("bottom", self._motors[0])
            plot_item.setLabel("left", self._motors[1])

        logger.debug(
            "Scatter: plotted '{}' color on ({}, {}), {} points",
            self._field_name, self._motors[0], self._motors[1], n,
        )
