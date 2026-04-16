"""Adaptive hyperparameter line-plot visualization.

Reads GP hyperparameters from each ``iter_NNN`` container written by
Tsuchinoko's TiledPublisher and plots them as lines (one per component)
over iteration index.

Ported from Phase 4b ``feature/tsuchinoko-gp-viz`` to the post-viz-cleanup
BaseVisualization ABC.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QVBoxLayout, QWidget

from lucid.visualization.base_visualization import BaseVisualization

_ITER_RE = re.compile(r"^iter_\d{3}$")
_STALE_POLL_LIMIT = 3
_POLL_INTERVAL_MS = 2000

# Palette for up to ~10 hyperparameter lines
_LINE_COLORS = [
    (31, 119, 180),   # muted blue
    (255, 127, 14),   # orange
    (44, 160, 44),    # green
    (214, 39, 40),    # red
    (148, 103, 189),  # purple
    (140, 86, 75),    # brown
    (227, 119, 194),  # pink
    (127, 127, 127),  # gray
    (188, 189, 34),   # olive
    (23, 190, 207),   # cyan
]


def _discover_iterations(container: Any) -> list[str]:
    return sorted(k for k in container.keys() if _ITER_RE.match(k))


class AdaptivePlotVisualization(BaseVisualization):
    """Line plot of GP hyperparameters across adaptive iterations."""

    viz_name = "adaptive_plot"
    viz_display_name = "Adaptive Plot"
    viz_icon = "chart-line"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._adaptive: Any | None = None
        self._iters: list[str] = []
        self._iter_data: dict[str, np.ndarray] = {}
        self._stale_count: int = 0

        self._plot_widget: pg.PlotWidget | None = None
        self._legend: pg.LegendItem | None = None
        self._lines: list[pg.PlotDataItem] = []

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_tick)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._plot_widget = pg.PlotWidget()
        plot_item = self._plot_widget.getPlotItem()
        if plot_item:
            plot_item.setLabel("bottom", "Iteration")
            plot_item.setLabel("left", "Value")
            plot_item.showGrid(x=True, y=True, alpha=0.3)

        self._legend = self._plot_widget.addLegend()
        layout.addWidget(self._plot_widget)

    # ------------------------------------------------------------------
    # BaseVisualization interface
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Return 70 for Tsuchinoko run with hyperparameters, 0 otherwise."""
        try:
            adaptive = run["adaptive"]
            if adaptive.metadata.get("adaptive_engine") != "tsuchinoko":
                return 0
            # Check at least one iter has hyperparameters
            for key in adaptive.keys():
                if _ITER_RE.match(key):
                    if "hyperparameters" in adaptive[key]:
                        return 70
        except Exception:
            pass
        return 0

    def set_run(self, run: Any) -> None:
        self._run = run

    def get_streams(self) -> list[str]:
        return ["adaptive"]

    def set_stream(self, stream_name: str) -> None:
        self._stream_name = stream_name
        if self._run is None:
            return

        try:
            self._adaptive = self._run["adaptive"]
        except Exception:
            self._adaptive = None
            return

        # Read all existing iterations
        self._iters = _discover_iterations(self._adaptive)
        self._iter_data.clear()
        for iter_key in self._iters:
            try:
                node = self._adaptive[iter_key]
                if "hyperparameters" in node:
                    self._iter_data[iter_key] = np.asarray(node["hyperparameters"].read())
            except Exception:
                pass

        self._stale_count = 0
        self._poll_timer.start()

        # Auto-pick field and render
        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        """Return available plot-able field names."""
        if self._iter_data:
            return ["hyperparameters"]
        return []

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self._rebuild_plot()

    def refresh(self) -> None:
        self._poll_tick()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_tick(self) -> None:
        if self._adaptive is None:
            return

        new_iters = _discover_iterations(self._adaptive)
        new_found = False
        for iter_key in new_iters:
            if iter_key not in self._iter_data:
                try:
                    node = self._adaptive[iter_key]
                    if "hyperparameters" in node:
                        self._iter_data[iter_key] = np.asarray(
                            node["hyperparameters"].read()
                        )
                        new_found = True
                except Exception:
                    pass

        if new_found:
            self._stale_count = 0
            self._iters = new_iters
            self._rebuild_plot()
        else:
            self._stale_count += 1
            if self._stale_count >= _STALE_POLL_LIMIT:
                self._poll_timer.stop()
                logger.debug("AdaptivePlot: polling stopped (stale)")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _rebuild_plot(self) -> None:
        if not self._iter_data or self._plot_widget is None:
            return

        sorted_keys = sorted(self._iter_data.keys())
        iters = [int(k.split("_")[1]) for k in sorted_keys]
        values = np.array([self._iter_data[k] for k in sorted_keys])

        if values.ndim == 1:
            values = values.reshape(-1, 1)

        n_hp = values.shape[1]
        x = np.array(iters, dtype=float)

        # Grow line list as needed
        while len(self._lines) < n_hp:
            idx = len(self._lines)
            color = _LINE_COLORS[idx % len(_LINE_COLORS)]
            pen = pg.mkPen(color=color, width=2)
            line = self._plot_widget.plot([], [], pen=pen, name=f"hp[{idx}]")
            self._lines.append(line)

        # Update data
        for i in range(n_hp):
            self._lines[i].setData(x, values[:, i])
            self._lines[i].setVisible(True)

        # Hide excess lines
        for i in range(n_hp, len(self._lines)):
            self._lines[i].setVisible(False)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        super().closeEvent(event)
