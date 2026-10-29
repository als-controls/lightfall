"""1D line plot visualization on the new BaseVisualization ABC.

Reads scalar data from a tiled BlueskyRun's internal/events table
and plots one or more dependent fields against a motor or seq_num.

A checkable field list beside the plot picks which fields are overlaid;
every checked field is drawn as its own curve on a shared y-axis.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.utils.tiled_helpers import stream_data_keys
from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.theme import VisualizationColors

_LINE_COLORS = VisualizationColors().line_colors


class Plot1DVisualization(BaseVisualization):
    """Tiled-backed 1D line plot.

    Displays a scalar field vs a motor axis (or seq_num) read from
    the stream's internal/events table.
    """

    viz_name = "plot_1d"
    viz_display_name = "1D Plot"
    viz_icon = "chart-line"

    # Field-name prefixes that are ticked together by default, and whose
    # presence makes 1D Plot the auto-selected visualization.
    overlay_default_prefixes: tuple[str, ...] = ("B_analog_inputs",)

    @classmethod
    def _is_overlay_default(cls, name: str) -> bool:
        return name.startswith(cls.overlay_default_prefixes)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Tiled state
        self._stream: Any | None = None
        self._data_keys: dict[str, Any] = {}
        self._motors: list[str] = []
        # Fields currently plotted, in field-list order.
        self._checked_fields: list[str] = []

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        layout.addLayout(self._build_toolbar())

        body = QHBoxLayout()
        body.setSpacing(4)

        # Checkable field list: tick fields to overlay them on one plot.
        self._field_list = QListWidget()
        self._field_list.setObjectName("plot1d_field_list")
        self._field_list.setToolTip("Tick fields to plot them together")
        self._field_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._field_list.setMaximumWidth(220)
        self._field_list.itemChanged.connect(self._on_field_item_changed)
        self._field_list.setVisible(False)
        body.addWidget(self._field_list)

        self._plot_widget = pg.PlotWidget()
        plot_item = self._plot_widget.getPlotItem()
        if plot_item:
            plot_item.addLegend()
            plot_item.showGrid(x=True, y=True, alpha=0.3)
        body.addWidget(self._plot_widget, 1)
        layout.addLayout(body)

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
            data_keys = stream_data_keys(run["primary"])
        except Exception:
            return 0
        # Runs carrying an overlay-group field (e.g. B_analog_inputs_*) open
        # in 1D Plot even when an image detector is present (beats
        # ScanViewer's 90).
        if any(
            Plot1DVisualization._is_overlay_default(name)
            and list(dk.get("shape") or []) == []
            for name, dk in data_keys.items()
        ):
            return 95
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
            self._data_keys = stream_data_keys(self._stream)
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
        self._populate_field_list(fields)
        overlay = [f for f in fields if self._is_overlay_default(f)]
        if overlay:
            # Tick the whole overlay group by default.
            self._field_name = overlay[0]
            self.set_checked_fields(overlay)
        elif fields:
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
        """Plot just ``field_name`` (the panel's Field combo calls this)."""
        self._field_name = field_name
        self.set_checked_fields([field_name] if field_name else [])

    def set_checked_fields(self, fields: list[str]) -> None:
        """Overlay exactly ``fields`` (names not in the stream are ignored)."""
        wanted = set(fields)
        self._field_list.blockSignals(True)
        for i in range(self._field_list.count()):
            item = self._field_list.item(i)
            item.setCheckState(
                Qt.CheckState.Checked if item.text() in wanted
                else Qt.CheckState.Unchecked
            )
        self._field_list.blockSignals(False)
        self._sync_checked_from_list()
        self._replot()

    def checked_fields(self) -> list[str]:
        """Fields currently plotted."""
        return list(self._checked_fields)

    def export_columns(self) -> dict[str, np.ndarray]:
        """Export what is plotted: the X column, then each ticked field."""
        if not self._checked_fields:
            raise ValueError("No fields are selected to export")
        events = self._read_events_table()
        if events is None:
            raise ValueError("Could not read the stream data")
        x_field = self._x_combo.currentText() if self._x_combo.count() else "seq_num"
        ys: dict[str, np.ndarray] = {}
        for name in self._checked_fields:
            try:
                ys[name] = np.asarray(events[name], dtype=np.float64)
            except Exception:
                continue
        if not ys:
            raise ValueError("None of the selected fields could be read")
        n_events = max(len(v) for v in ys.values())
        if x_field == "seq_num":
            x = np.arange(1, n_events + 1, dtype=np.int64)
        else:
            try:
                x = np.asarray(events[x_field], dtype=np.float64)
            except Exception:
                x_field, x = "seq_num", np.arange(1, n_events + 1, dtype=np.int64)
        return {x_field: x, **{k: v for k, v in ys.items() if k != x_field}}

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

    def _populate_field_list(self, fields: list[str]) -> None:
        self._field_list.blockSignals(True)
        self._field_list.clear()
        for i, name in enumerate(fields):
            item = QListWidgetItem(name)
            item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(Qt.CheckState.Unchecked)
            swatch = QPixmap(12, 12)
            swatch.fill(QColor(self._color_for(i)))
            item.setIcon(QIcon(swatch))
            self._field_list.addItem(item)
        self._field_list.blockSignals(False)
        self._field_list.setVisible(len(fields) > 1)
        self._checked_fields = []

    @staticmethod
    def _color_for(index: int) -> str:
        return _LINE_COLORS[index % len(_LINE_COLORS)]

    def _sync_checked_from_list(self) -> None:
        self._checked_fields = [
            self._field_list.item(i).text()
            for i in range(self._field_list.count())
            if self._field_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        # Field list empty (e.g. set_field before set_stream): honour the field.
        if self._field_list.count() == 0 and self._field_name:
            self._checked_fields = [self._field_name]

    def _on_field_item_changed(self, _item: QListWidgetItem) -> None:
        self._sync_checked_from_list()
        if self._checked_fields and self._field_name not in self._checked_fields:
            # Keep the panel's "active field" pointing at something plotted.
            self._field_name = self._checked_fields[0]
        self._replot()

    def _field_color(self, name: str, fallback_index: int) -> str:
        items = self._field_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            return self._color_for(self._field_list.row(items[0]))
        return self._color_for(fallback_index)

    def _y_axis_label(self, fields: list[str]) -> str:
        if len(fields) == 1:
            return fields[0]
        units = {self._data_keys.get(f, {}).get("units") or "" for f in fields}
        if len(units) == 1 and "" not in units:
            return f"value ({units.pop()})"
        return "value"

    def _read_events_table(self):
        """Read the plotted columns (X axis + ticked fields) from the stream.

        Only those columns are requested, so image data in the same stream
        is never downloaded.
        """
        from lightfall.utils.tiled_helpers import read_events

        wanted = [*self._checked_fields]
        x_field = self._x_combo.currentText() if self._x_combo.count() else ""
        if x_field and x_field != "seq_num":
            wanted.append(x_field)
        wanted = [f for f in dict.fromkeys(wanted) if f in self._data_keys]
        return read_events(self._stream, variables=wanted or None)

    def _on_x_changed(self, _: str) -> None:
        self._replot()

    def _on_markers_toggled(self, _: bool) -> None:
        self._replot()

    def _replot(self) -> None:
        fields = list(self._checked_fields)

        x_field = self._x_combo.currentText() if self._x_combo.count() else "seq_num"
        plot_item = self._plot_widget.getPlotItem()

        if not fields:
            self._plot_widget.clear()
            return

        events = self._read_events_table()
        if events is None:
            return

        series: list[tuple[str, np.ndarray]] = []
        for name in fields:
            try:
                series.append((name, np.asarray(events[name], dtype=np.float64)))
            except Exception as e:
                logger.debug("Plot1D: field '{}' not in events: {}", name, e)

        self._plot_widget.clear()
        if plot_item:
            plot_item.setLabel("bottom", x_field)
            plot_item.setLabel("left", self._y_axis_label([n for n, _ in series]))
        if not series:
            return

        n_events = max(len(y) for _, y in series)
        if x_field == "seq_num":
            x_all = np.arange(1, n_events + 1, dtype=np.float64)
        else:
            try:
                x_all = np.asarray(events[x_field], dtype=np.float64)
            except Exception:
                x_all = np.arange(1, n_events + 1, dtype=np.float64)

        markers = self._markers_button.isChecked()
        for i, (name, y_arr) in enumerate(series):
            color = self._field_color(name, i)
            n = min(len(x_all), len(y_arr))
            self._plot_widget.plot(
                x_all[:n],
                y_arr[:n],
                pen=pg.mkPen(color=color, width=2),
                symbol="o" if markers else None,
                symbolSize=6,
                symbolBrush=color,
                symbolPen=pg.mkPen(color=color),
                name=name,
            )

        logger.debug(
            "Plot1D: plotted {} vs '{}', {} events",
            [n for n, _ in series], x_field, n_events,
        )
