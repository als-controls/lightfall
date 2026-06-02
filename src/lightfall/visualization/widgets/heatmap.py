"""2D heatmap visualization on the new BaseVisualization ABC.

Reads scalar data from a tiled BlueskyRun's internal/events table
and reshapes it into a grid using the scan shape from start metadata.
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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.visualization.base_visualization import BaseVisualization


class HeatmapVisualization(BaseVisualization):
    """Tiled-backed 2D heatmap.

    Reshapes a scalar field from the events table into a 2D grid using
    the scan shape recorded in run.metadata["start"]["shape"].
    """

    viz_name = "heatmap"
    viz_display_name = "Heatmap"
    viz_icon = "grid"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Tiled state
        self._stream: Any | None = None
        self._data_keys: dict[str, Any] = {}
        self._motors: list[str] = []
        self._scan_shape: tuple[int, ...] = ()

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
            plot_item.setAspectLocked(True)

        self._image_item = pg.ImageItem()
        self._plot_widget.addItem(self._image_item)

        # Apply default colormap
        self._apply_colormap("viridis")

        layout.addWidget(self._plot_widget)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("Colormap:"))
        self._cmap_combo = QComboBox()
        self._cmap_combo.addItems([
            "viridis", "plasma", "inferno", "magma", "cividis",
            "gray", "hot", "cool",
        ])
        self._cmap_combo.currentTextChanged.connect(self._apply_colormap)
        toolbar.addWidget(self._cmap_combo)

        toolbar.addStretch()

        auto_btn = QPushButton("Auto Range")
        auto_btn.clicked.connect(self._on_auto_range)
        toolbar.addWidget(auto_btn)

        return toolbar

    # ------------------------------------------------------------------
    # BaseVisualization interface
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Return 85 for 2D rectilinear, 30 for 2D non-rectilinear, 0 otherwise."""
        try:
            start = run.metadata.get("start", {})
            hints = start.get("hints", {})
            dims = hints.get("dimensions", [])
        except Exception:
            return 0

        if len(dims) != 2:
            return 0

        # Rectilinear: shape exists and has two positive integers
        try:
            shape = start.get("shape", [])
            if len(shape) >= 2 and shape[0] > 0 and shape[1] > 0:
                return 85
        except Exception:
            pass

        return 30

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
            logger.debug("Heatmap: could not open stream '{}': {}", stream_name, e)
            self._data_keys = {}

        self._motors = self._detect_motors()

        # Cache scan shape
        try:
            shape = self._run.metadata.get("start", {}).get("shape", [])
            self._scan_shape = tuple(int(s) for s in shape)
        except Exception:
            self._scan_shape = ()

        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        """Return scalar fields, hinted first, motors excluded."""
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
        from lightfall.utils.tiled_helpers import read_events
        return read_events(self._stream)

    def _apply_colormap(self, cmap_name: str) -> None:
        try:
            cmap = pg.colormap.get(cmap_name)
            if cmap and self._image_item:
                lut = cmap.getLookupTable(nPts=256)
                self._image_item.setLookupTable(lut)
        except Exception as e:
            logger.debug("Heatmap: could not apply colormap '{}': {}", cmap_name, e)

    def _on_auto_range(self) -> None:
        if self._plot_widget:
            self._plot_widget.autoRange()

    def _replot(self) -> None:
        if not self._field_name:
            return

        events = self._read_events_table()
        if events is None:
            return

        try:
            z_arr = np.asarray(events[self._field_name], dtype=np.float64)
        except Exception as e:
            logger.debug("Heatmap: field '{}' not in events: {}", self._field_name, e)
            return

        # Determine grid shape
        if len(self._scan_shape) >= 2:
            grid_shape = (self._scan_shape[0], self._scan_shape[1])
        else:
            side = max(1, int(np.sqrt(len(z_arr))))
            grid_shape = (side, side)

        grid = np.full(grid_shape, np.nan, dtype=np.float64)
        n = min(len(z_arr), grid_shape[0] * grid_shape[1])
        ny = grid_shape[1]
        for i in range(n):
            ix = i // ny
            iy = i % ny
            if ix < grid_shape[0] and iy < grid_shape[1]:
                grid[ix, iy] = z_arr[i]

        # ImageItem expects (cols, rows) for display — transpose for row-major
        self._image_item.setImage(grid.T)

        plot_item = self._plot_widget.getPlotItem()
        if plot_item and len(self._motors) >= 2:
            plot_item.setLabel("bottom", self._motors[1])
            plot_item.setLabel("left", self._motors[0])

        logger.debug(
            "Heatmap: plotted '{}', shape {}, {} points filled",
            self._field_name, grid_shape, n,
        )
