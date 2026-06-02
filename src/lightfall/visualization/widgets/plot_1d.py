"""1D line plot visualization on the new BaseVisualization ABC.

Reads scalar data from a tiled BlueskyRun's internal/events table
and plots a selected dependent field against a motor or seq_num.
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.visualization.base_visualization import BaseVisualization


class Plot1DVisualization(BaseVisualization):
    """Tiled-backed 1D line plot.

    Displays a scalar field vs a motor axis (or seq_num) read from
    the stream's internal/events table.
    """

    viz_name = "plot_1d"
    viz_display_name = "1D Plot"
    viz_icon = "chart-line"

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
            plot_item.addLegend()
            plot_item.showGrid(x=True, y=True, alpha=0.3)
        layout.addWidget(self._plot_widget)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("X:"))
        self._x_combo = QComboBox()
        self._x_combo.setMinimumWidth(100)
        self._x_combo.currentTextChanged.connect(self._on_x_changed)
        toolbar.addWidget(self._x_combo)

        self._markers_button = QToolButton()
        self._markers_button.setText("Markers")
        self._markers_button.setCheckable(True)
        self._markers_button.setChecked(True)
        self._markers_button.toggled.connect(self._on_markers_toggled)
        toolbar.addWidget(self._markers_button)

        toolbar.addStretch()
        return toolbar

    # ------------------------------------------------------------------
    # BaseVisualization interface
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Return 80 if run has a 1D scan with scalar dependent fields."""
        try:
            start = run.metadata.get("start", {})
            hints = start.get("hints", {})
            dims = hints.get("dimensions", [])
        except Exception:
            return 0

        if len(dims) > 1:
            return 0

        # Check primary stream for scalar data_keys
        try:
            data_keys = run["primary"].metadata.get("data_keys", {})
        except Exception:
            return 0
        for dk in data_keys.values():
            shape = dk.get("shape", [])
            if shape == [] or shape == ():
                return 80
        return 0

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
            logger.debug("Plot1D: could not open stream '{}': {}", stream_name, e)
            self._data_keys = {}

        # Detect motor fields from start hints
        self._motors = self._detect_motors()

        # Populate X combo
        self._x_combo.blockSignals(True)
        self._x_combo.clear()
        x_choices = self._motors + ["seq_num"]
        self._x_combo.addItems(x_choices)
        self._x_combo.blockSignals(False)

        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        """Return scalar (shape=[]) fields, hinted first, motors excluded."""
        if not self._data_keys:
            return []

        try:
            hints = self._stream.metadata.get("hints", {}).get("fields", [])
            hinted = set(hints)
        except Exception:
            hinted = set()

        motor_set = set(self._motors)
        hinted_scalars: list[str] = []
        other_scalars: list[str] = []

        for name, dk in self._data_keys.items():
            if name in motor_set:
                continue
            shape = dk.get("shape", [])
            dtype = dk.get("dtype", "")
            is_scalar = shape == [] or shape == ()
            is_numeric = dtype in ("number", "integer", "float", "int", "")
            if is_scalar and is_numeric:
                if name in hinted:
                    hinted_scalars.append(name)
                else:
                    other_scalars.append(name)

        return hinted_scalars + other_scalars

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self._replot()

    def refresh(self) -> None:
        self._replot()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_motors(self) -> list[str]:
        """Return motor field names from start hints."""
        motors: list[str] = []
        try:
            start = self._run.metadata.get("start", {})
            hints = start.get("hints", {})
            dims = hints.get("dimensions", [])
            for fields, _stream in dims:
                motors.extend(fields)
        except Exception:
            pass
        return motors

    def _read_events_table(self):
        """Read the data table from the current stream (V3 or V2)."""
        from lightfall.utils.tiled_helpers import read_events
        return read_events(self._stream)

    def _on_x_changed(self, _: str) -> None:
        self._replot()

    def _on_markers_toggled(self, _: bool) -> None:
        self._replot()

    def _replot(self) -> None:
        if not self._field_name:
            return

        events = self._read_events_table()
        if events is None:
            return

        y_arr = None
        try:
            y_arr = np.asarray(events[self._field_name], dtype=np.float64)
        except Exception as e:
            logger.debug("Plot1D: field '{}' not in events: {}", self._field_name, e)
            return

        x_field = self._x_combo.currentText() if self._x_combo.count() else "seq_num"
        if x_field == "seq_num":
            x_arr = np.arange(1, len(y_arr) + 1, dtype=np.float64)
        else:
            try:
                x_arr = np.asarray(events[x_field], dtype=np.float64)
            except Exception:
                x_arr = np.arange(1, len(y_arr) + 1, dtype=np.float64)

        n = min(len(x_arr), len(y_arr))
        x_arr = x_arr[:n]
        y_arr = y_arr[:n]

        self._plot_widget.clear()
        plot_item = self._plot_widget.getPlotItem()
        if plot_item:
            plot_item.setLabel("bottom", x_field)
            plot_item.setLabel("left", self._field_name)

        markers = self._markers_button.isChecked()
        self._plot_widget.plot(
            x_arr,
            y_arr,
            pen=pg.mkPen(color="#3b82f6", width=2),
            symbol="o" if markers else None,
            symbolSize=6,
            symbolBrush="#3b82f6",
            symbolPen=pg.mkPen(color="#3b82f6"),
            name=self._field_name,
        )

        logger.debug(
            "Plot1D: plotted '{}' vs '{}', {} points",
            self._field_name, x_field, n,
        )
