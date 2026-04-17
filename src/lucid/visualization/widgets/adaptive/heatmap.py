"""Adaptive 2D heatmap visualization (posterior mean/variance/acquisition).

Reads from the ``adaptive`` BlueskyEventStream written by Tsuchinoko's
TiledPublisher.  Per-iteration GP data is stored as zarr arrays (one
per field, indexed by event number).  Evaluation grids are in the
descriptor's ``configuration.tsuchinoko.data``.

Uses :class:`LazyImageView` for display: each iteration is fetched on
demand, the histogram provides LUT control, and the standard
image-view toolbar buttons (Reset LUT, Reset Axes, Log Intensity, ROI)
are supplied by :class:`ImageViewToolbarMixin`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtCore import QRectF, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from lucid.visualization.base_visualization import BaseVisualization
from lucid.visualization.widgets.image_view_toolbar import ImageViewToolbarMixin
from lucid.visualization.widgets.lazy_image_view import LazyImageView
from lucid.visualization.widgets.time_axis import HumanReadableTimeAxis

# Fields in priority order — only those actually present are offered.
_HEATMAP_FIELDS = ["posterior_mean", "posterior_variance", "acquisition_function"]

_STALE_POLL_LIMIT = 3
_POLL_INTERVAL_MS = 2000


class AdaptiveHeatmapVisualization(ImageViewToolbarMixin, BaseVisualization):
    """2D heatmap of GP posterior arrays from an adaptive experiment.

    Renders posterior_mean, posterior_variance, or acquisition_function
    for a selected iteration, with optional measurement and target overlays.
    The iteration slider is replaced by the ImageView timeline scrubber.
    """

    viz_name = "adaptive_heatmap"
    viz_display_name = "Adaptive Heatmap"
    viz_icon = "grid"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_image_view_toolbar_state()

        # Tiled state
        self._adaptive: Any | None = None
        self._grid_x: np.ndarray | None = None
        self._grid_y: np.ndarray | None = None
        self._grid_shape: list[int] | None = None
        self._n_iterations: int = 0
        self._current_index: int = -1
        self._stale_count: int = 0
        self._frame_shape: tuple[int, ...] = ()

        # Overlay scatter items (populated in _build_ui)
        self._meas_scatter: pg.ScatterPlotItem | None = None
        self._target_scatter: pg.ScatterPlotItem | None = None

        # Polling timer
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_tick)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        layout.addLayout(self._build_toolbar())

        # Lazy image view (same pattern as ImageStackVisualization)
        self._image_view = LazyImageView()
        self._image_view.ui.roiPlot.show()
        self._image_view.ui.roiPlot.setMinimumHeight(80)
        self._image_view.ui.splitter.setSizes([400, 100])

        # Iteration axis on the timeline
        self._time_axis = HumanReadableTimeAxis(orientation="bottom")
        self._image_view.ui.roiPlot.setAxisItems({"bottom": self._time_axis})

        self._image_view.sigTimeChanged.connect(self._on_iteration_changed)

        # Style the scrubber bar
        timeline = self._image_view.timeLine
        timeline.setPen(pg.mkPen("y", width=3))
        timeline.setHoverPen(pg.mkPen("y", width=5))

        # Hide built-in ROI / menu buttons (we provide our own)
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()

        # Default colormap
        cmap = pg.colormap.get("viridis")
        if cmap:
            self._image_view.setColorMap(cmap)

        # Overlay scatter items — added to the ImageView's PlotItem
        self._meas_scatter = pg.ScatterPlotItem(
            pen=None, symbol="o", size=6,
            brush=pg.mkBrush(255, 255, 255, 120),
        )
        self._target_scatter = pg.ScatterPlotItem(
            pen=pg.mkPen("r", width=1.5), brush=None, symbol="x", size=10,
        )
        self._image_view.addItem(self._meas_scatter)
        self._image_view.addItem(self._target_scatter)

        layout.addWidget(self._image_view)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Standard image-view buttons (Reset LUT, Reset Axes, Log, ROI)
        self._build_image_view_buttons(toolbar)

        toolbar.addStretch()

        # Overlay toggles
        self._meas_check = QCheckBox("Measurements")
        self._meas_check.setChecked(True)
        self._meas_check.toggled.connect(self._on_overlay_toggled)
        toolbar.addWidget(self._meas_check)

        self._target_check = QCheckBox("Targets")
        self._target_check.setChecked(True)
        self._target_check.toggled.connect(self._on_overlay_toggled)
        toolbar.addWidget(self._target_check)

        return toolbar

    # ------------------------------------------------------------------
    # BaseVisualization interface
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Return 90 for a 2D Tsuchinoko adaptive run, 0 otherwise."""
        try:
            adaptive = run["adaptive"]
            if adaptive.metadata.get("adaptive_engine") != "tsuchinoko":
                return 0
            config = adaptive.metadata.get("configuration", {})
            tsuchinoko_config = config.get("tsuchinoko", {}).get("data", {})
            has_x = "evaluation_grid_x" in tsuchinoko_config
            has_y = "evaluation_grid_y" in tsuchinoko_config
            has_z = "evaluation_grid_z" in tsuchinoko_config
            if has_x and has_y and not has_z:
                return 90
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

        # Read evaluation grids from descriptor configuration
        try:
            config = self._adaptive.metadata.get("configuration", {})
            tsuchinoko_data = config.get("tsuchinoko", {}).get("data", {})
            self._grid_x = np.asarray(tsuchinoko_data["evaluation_grid_x"])
            self._grid_y = np.asarray(tsuchinoko_data["evaluation_grid_y"])
        except Exception as exc:
            logger.debug("AdaptiveHeatmap: could not read grid config: {}", exc)
            self._grid_x = self._grid_y = None

        # Get grid shape from data_keys metadata
        try:
            dk = self._adaptive.metadata.get("data_keys", {})
            self._grid_shape = dk.get("posterior_mean", {}).get("grid_shape")
        except Exception:
            self._grid_shape = None

        # Start polling
        self._stale_count = 0
        self._poll_timer.start()

        # Auto-pick best field
        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        """Return the subset of heatmap fields present in the stream."""
        if self._adaptive is None:
            return []
        try:
            available = list(self._adaptive)
        except Exception:
            return []
        return [f for f in _HEATMAP_FIELDS if f in available]

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name

        if self._adaptive is None or field_name not in self._adaptive:
            return

        # Count iterations from array shape
        try:
            arr = self._adaptive[field_name]
            arr_shape = arr.shape
            n = arr_shape[0]
        except Exception as exc:
            logger.debug("AdaptiveHeatmap: cannot read shape for '{}': {}", field_name, exc)
            return

        self._n_iterations = n
        if n == 0:
            return

        # Determine grid/frame shape
        if self._grid_shape:
            gs = tuple(self._grid_shape)
        else:
            flat_size = arr_shape[1] if len(arr_shape) > 1 else 0
            side = int(np.sqrt(flat_size))
            gs = (side, side) if side * side == flat_size else None

        if gs is None:
            logger.warning("AdaptiveHeatmap: cannot determine grid shape for '{}'", field_name)
            return

        # Transposed frame shape for col-major display (matches the
        # previous data.T behaviour with the PlotWidget ImageItem).
        frame_shape = (gs[1], gs[0])
        self._frame_shape = frame_shape

        # Build a closure that fetches one iteration via server-side
        # slicing (avoids downloading the full chunk).
        from lucid.utils.tiled_helpers import fetch_frame as _fetch_frame

        arr_client = arr
        grid_shape = gs

        def fetch_iteration(index: int) -> np.ndarray:
            flat = _fetch_frame(arr_client, index)
            return flat.reshape(grid_shape).T

        # Hand off to LazyImageView — the real ArrayClient provides
        # .shape[0] for frame count; fetch_func handles the rest.
        timestamps = np.arange(n, dtype=np.float64)

        self._image_view.setArraySource(
            arr_client, timestamps, frame_shape, fetch_func=fetch_iteration,
        )

        # Display the latest iteration
        self._image_view.setCurrentIndex(n - 1)
        self._apply_grid_rect()
        self._on_reset_lut()
        self._on_reset_axes()

        self._current_index = n - 1
        self._update_overlays()
        self._update_status()

    def refresh(self) -> None:
        self._poll_tick()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_tick(self) -> None:
        """Check for new iterations and update display."""
        if self._adaptive is None or not self._field_name:
            return

        was_at_end = (
            self._current_index == self._n_iterations - 1
            if self._n_iterations
            else True
        )

        old_count = self._n_iterations

        try:
            n = 0
            for field in _HEATMAP_FIELDS:
                if field in self._adaptive:
                    n = self._adaptive[field].shape[0]
                    break
            self._n_iterations = n
        except Exception:
            return

        if self._n_iterations == old_count:
            self._stale_count += 1
            if self._stale_count >= _STALE_POLL_LIMIT:
                self._poll_timer.stop()
                logger.debug("AdaptiveHeatmap: polling stopped (stale)")
            return

        # New iterations arrived
        self._stale_count = 0

        timestamps = np.arange(self._n_iterations, dtype=np.float64)
        self._image_view.updateFrameCount(self._n_iterations, timestamps)

        if was_at_end:
            new_index = self._n_iterations - 1
            self._image_view.setCurrentIndex(new_index)
            self._current_index = new_index
            self._apply_grid_rect()
            self._update_overlays()

        self._update_status()

    # ------------------------------------------------------------------
    # Iteration / overlay callbacks
    # ------------------------------------------------------------------

    def _on_iteration_changed(self, ind: int, _time: float) -> None:
        """Called when the user scrubs the timeline."""
        self._current_index = ind
        self._update_overlays()
        self._update_status()

    def _on_overlay_toggled(self, _checked: bool) -> None:
        self._update_overlays()

    def _apply_grid_rect(self) -> None:
        """Map pixel coordinates to physical grid coordinates via setRect."""
        if self._grid_x is None or self._grid_y is None:
            return
        x0, x1 = float(self._grid_x[0]), float(self._grid_x[-1])
        y0, y1 = float(self._grid_y[0]), float(self._grid_y[-1])
        self._image_view.imageItem.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))

    def _update_overlays(self) -> None:
        """Refresh both scatter overlays for the current iteration."""
        self._update_target_overlay()
        self._update_measurement_overlay()

    def _update_target_overlay(self) -> None:
        if self._target_scatter is None:
            return
        if not self._target_check.isChecked():
            self._target_scatter.clear()
            return
        if self._adaptive is None or self._current_index < 0:
            self._target_scatter.clear()
            return
        try:
            if "targets" in self._adaptive:
                targets = np.asarray(self._adaptive["targets"][self._current_index])
                if len(targets) > 0:
                    if targets.ndim == 2 and targets.shape[1] >= 2:
                        self._target_scatter.setData(x=targets[:, 0], y=targets[:, 1])
                    elif targets.ndim == 1 and len(targets) >= 2:
                        self._target_scatter.setData(x=[targets[0]], y=[targets[1]])
                    else:
                        self._target_scatter.clear()
                else:
                    self._target_scatter.clear()
            else:
                self._target_scatter.clear()
        except Exception:
            self._target_scatter.clear()

    def _update_measurement_overlay(self) -> None:
        """Draw measured points from primary stream."""
        if self._meas_scatter is None or not self._meas_check.isChecked():
            if self._meas_scatter is not None:
                self._meas_scatter.clear()
            return
        if self._run is None:
            return

        # Resolve X/Y field names from start.hints.dimensions
        try:
            start = self._run.metadata.get("start", {})
            dims = start.get("hints", {}).get("dimensions", [])
            x_field = dims[0][0][0]
            y_field = dims[1][0][0]
        except (IndexError, KeyError, TypeError):
            self._meas_scatter.clear()
            return

        try:
            primary = self._run["primary"]
            x_data = np.asarray(primary[x_field].read())
            y_data = np.asarray(primary[y_field].read())
            if len(x_data) > 0:
                self._meas_scatter.setData(x=x_data, y=y_data)
            else:
                self._meas_scatter.clear()
        except Exception:
            self._meas_scatter.clear()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        current_idx = self._image_view.currentIndex if self._image_view else 0
        total = self._n_iterations
        current = current_idx + 1 if total > 0 else 0
        self._time_axis.setLabel(f"Iteration {current}/{total}")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        super().closeEvent(event)
