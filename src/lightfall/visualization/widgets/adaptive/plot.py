"""GP hyperparameter line plot for Tsuchinoko adaptive runs.

Plots each GP hyperparameter (signal variance, length scales, ...) against
iteration so a scientist can see whether the model has converged or a
length scale is running away.

Data layout (see :mod:`.schema`): Tsuchinoko's TiledPublisher writes one
event per iteration to the ``adaptive`` stream, so ``hyperparameters`` is a
zarr array of shape ``(n_iterations, n_hp)``.  Columns that are NaN in every
row are declared-shape padding and are dropped; all-NaN rows are iterations
where the GP had no hyperparameters yet and are kept as gaps.

Live updates are driven by the VisualizationPanel: it subscribes the active
field's array node and calls :meth:`refresh` on each push.  The Tiled read
always runs on a :class:`QThreadFuture`; only the plot update touches the GUI
thread.

Originally Phase 4b's ``HyperparameterVisualization`` (``feature/
tsuchinoko-gp-viz``), which read the long-gone ``iter_NNN`` container layout.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from loguru import logger
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from lightfall.utils.crash_diagnostics import gui_thread_only
from lightfall.utils.threads import QThreadFuture
from lightfall.visualization import pg
from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.widgets.adaptive.schema import (
    adaptive_data_keys,
    adaptive_field_node,
    is_tsuchinoko_stream,
)

_FIELD = "hyperparameters"


def read_hyperparameters(adaptive: Any) -> np.ndarray:
    """Read the hyperparameter history as an ``(n_iterations, n_hp)`` array.

    Blocking Tiled I/O -- call off the GUI thread.  Raises ``KeyError`` if
    the stream has no ``hyperparameters`` array.
    """
    values = np.asarray(adaptive_field_node(adaptive, _FIELD).read(), dtype=float)
    if values.ndim == 0:
        values = values.reshape(1, 1)
    elif values.ndim == 1:
        values = values.reshape(-1, 1)
    elif values.ndim > 2:
        values = values.reshape(values.shape[0], -1)
    if values.size:
        values = values[:, ~np.isnan(values).all(axis=0)]
    return values


class AdaptivePlotVisualization(BaseVisualization):
    """Line plot of GP hyperparameters across adaptive iterations."""

    viz_name = "adaptive_plot"
    viz_display_name = "GP Hyperparameters"
    viz_icon = "chart-line"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._adaptive: Any | None = None
        self._n_iterations: int = 0
        self._lines: list[pg.PlotDataItem] = []
        self._n_active: int = 0  # lines currently carrying data
        self._future: QThreadFuture | None = None
        self._refresh_pending: bool = False

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        # Length scales routinely span orders of magnitude.
        self._log_btn = QPushButton("Log Y")
        self._log_btn.setCheckable(True)
        self._log_btn.setToolTip("Logarithmic value axis")
        self._log_btn.toggled.connect(self._on_log_toggled)
        toolbar.addWidget(self._log_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._plot_widget = pg.PlotWidget()
        plot_item = self._plot_widget.getPlotItem()
        plot_item.setLabel("bottom", "Iteration")
        plot_item.setLabel("left", "Value")
        plot_item.showGrid(x=True, y=True, alpha=0.3)
        # Clicking a legend entry toggles that line's visibility.
        self._legend = self._plot_widget.addLegend()
        layout.addWidget(self._plot_widget)

    def _on_log_toggled(self, checked: bool) -> None:
        self._plot_widget.getPlotItem().setLogMode(y=checked)

    # ------------------------------------------------------------------
    # BaseVisualization interface
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Return 70 for a Tsuchinoko run recording hyperparameters, else 0.

        Deliberately below AdaptiveHeatmapVisualization's 90 so the heatmap
        stays the default view; this one is offered in the dropdown.
        """
        try:
            adaptive = run["adaptive"]
            if is_tsuchinoko_stream(adaptive) and _FIELD in adaptive_data_keys(adaptive):
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
        self._adaptive = None
        self._n_iterations = 0
        self._clear_lines()
        if self._run is None:
            return
        try:
            self._adaptive = self._run["adaptive"]
        except Exception:
            return

        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        if self._adaptive is not None and _FIELD in adaptive_data_keys(self._adaptive):
            return [_FIELD]
        return []

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self.refresh()

    def refresh(self) -> None:
        """Re-read the hyperparameter history off the GUI thread."""
        if self._adaptive is None:
            return
        if self._future is not None:
            # A read is in flight; re-read once it lands so this push isn't lost.
            self._refresh_pending = True
            return
        self._refresh_pending = False
        adaptive = self._adaptive
        future = QThreadFuture(
            self._read_worker,
            adaptive,
            callback_slot=self._on_read_result,
            except_slot=self._on_read_error,
            log_exceptions=False,
            name="adaptive-hyperparameters",
        )
        future.finished.connect(self._on_read_finished)
        self._future = future
        future.start()

    # ------------------------------------------------------------------
    # Worker / result handling
    # ------------------------------------------------------------------

    @staticmethod
    def _read_worker(adaptive: Any) -> tuple[Any, np.ndarray]:
        # Module-level lookup so tests can patch read_hyperparameters.
        return adaptive, read_hyperparameters(adaptive)

    @gui_thread_only
    def _on_read_result(self, result: tuple[Any, np.ndarray]) -> None:
        adaptive, values = result
        if adaptive is not self._adaptive:
            return  # stream changed while the read was in flight
        self._apply_hyperparameters(values)

    def _on_read_error(self, exc: Exception) -> None:
        logger.debug("AdaptivePlot: hyperparameter read failed: {}", exc)

    @gui_thread_only
    def _on_read_finished(self) -> None:
        self._future = None
        if self._refresh_pending:
            self.refresh()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _apply_hyperparameters(self, values: np.ndarray) -> None:
        values = np.asarray(values, dtype=float)
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        n_iter, n_hp = values.shape if values.ndim == 2 else (0, 0)
        self._n_iterations = n_iter

        while len(self._lines) < n_hp:
            idx = len(self._lines)
            line = pg.PlotDataItem(
                pen=pg.mkPen(pg.series_pen(idx), width=2),
                name=f"hp[{idx}]",
                connect="finite",
            )
            self._plot_widget.addItem(line)
            self._lines.append(line)

        # 1-based to match the heatmap's "Iteration k/N" label.
        x = np.arange(1, n_iter + 1, dtype=float)
        for i, line in enumerate(self._lines):
            if i < n_hp:
                line.setData(x, values[:, i])
                # Only re-show lines *we* hid as excess; a line the user hid
                # via its legend entry stays hidden across refreshes.
                if i >= self._n_active:
                    line.show()
            else:
                line.setData([], [])
                line.hide()
        self._n_active = n_hp

    def _clear_lines(self) -> None:
        for line in self._lines:
            line.setData([], [])
            line.hide()
        self._n_active = 0
