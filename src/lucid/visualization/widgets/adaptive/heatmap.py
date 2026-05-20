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
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from lucid.utils.logging import log_time
from lucid.visualization.base_visualization import BaseVisualization
from lucid.visualization.widgets.image_view_toolbar import ImageViewToolbarMixin
from lucid.visualization.widgets.lazy_image_view import LazyImageView
from lucid.visualization.widgets.time_axis import HumanReadableTimeAxis

# Fields in priority order — only those actually present are offered.
_HEATMAP_FIELDS = ["posterior_mean", "posterior_variance", "acquisition_function"]



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
        self._frame_shape: tuple[int, ...] = ()

        # Overlay scatter items (populated in _build_ui)
        self._meas_scatter: pg.ScatterPlotItem | None = None
        self._target_scatter: pg.ScatterPlotItem | None = None

        # Cached measurement overlay data — doesn't change per-iteration,
        # only grows as new points are measured.  Refreshed on poll, not scrub.
        self._meas_x: np.ndarray | None = None
        self._meas_y: np.ndarray | None = None

        # Live-run subscription (replaces polling timer)
        self._subscription: Any | None = None

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

        # Cache measurement overlay data (one-time HTTP fetch)
        with log_time("set_stream: _refresh_measurement_cache", level="DEBUG"):
            self._refresh_measurement_cache()

        # Subscribe to live updates via tiled WebSocket instead of polling
        self._start_subscription()

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
        """Manual refresh — called by the panel's refresh timer.

        No-op when the WebSocket subscription is active (it handles
        updates with lower latency).  Only used as a fallback when
        the subscription couldn't be established.
        """
        if self._subscription is not None:
            return

        from lucid.utils.threads import QThreadFuture

        future = QThreadFuture(
            self._fetch_and_apply_new_iterations,
            except_slot=lambda e: logger.debug(
                "AdaptiveHeatmap: refresh failed: {}", e,
            ),
            register=False,
            name="refresh-fallback",
        )
        self._image_view._in_flight.add(future)
        future.finished.connect(lambda f=future: self._image_view._in_flight.discard(f))
        future.start()

    # ------------------------------------------------------------------
    # Live subscription
    # ------------------------------------------------------------------

    def _start_subscription(self) -> None:
        """Subscribe to the run container via tiled WebSocket.

        The subscription callback already runs on a background
        ``ThreadPoolExecutor``, so we do the HTTP-heavy work (reading
        ``.shape``, refreshing measurements) right there and only
        push the result dict to the main thread for the UI update.

        If the server doesn't support WebSocket streaming (e.g. no
        API-key endpoint), the subscription thread will fail silently
        and ``self._subscription`` is set back to ``None`` so that
        the panel's ``refresh()`` timer takes over.
        """
        self._stop_subscription()

        if self._run is None:
            return

        try:
            sub = self._run.subscribe()

            def on_update(update: Any) -> None:
                """Runs on the subscription's ThreadPoolExecutor."""
                logger.debug("AdaptiveHeatmap: subscription update: {}", update.type)
                self._fetch_and_apply_new_iterations()

            def on_disconnect(subscription: Any) -> None:
                logger.debug("AdaptiveHeatmap: subscription disconnected, "
                             "falling back to panel-driven refresh")
                self._subscription = None

            sub.child_created.add_callback(on_update)
            sub.child_metadata_updated.add_callback(on_update)
            sub.disconnected.add_callback(on_disconnect)

            self._subscription = sub

            # start_in_thread blocks until connected (or raises).
            # Wrap in try so that connection errors during the initial
            # handshake (e.g. server 500 on API-key creation) don't
            # produce an unhandled traceback on the subscription thread.
            try:
                sub.start_in_thread()
                logger.debug("AdaptiveHeatmap: WebSocket subscription started")
            except Exception as exc:
                logger.debug("AdaptiveHeatmap: subscription connect failed: {}", exc)
                self._subscription = None
                return
        except Exception as exc:
            logger.debug("AdaptiveHeatmap: subscription failed ({}), "
                         "falling back to panel-driven refresh", exc)
            self._subscription = None

    def _stop_subscription(self) -> None:
        """Disconnect the WebSocket subscription if active."""
        if self._subscription is not None:
            try:
                self._subscription.disconnect()
            except Exception:
                pass
            self._subscription = None

    def _fetch_and_apply_new_iterations(self) -> None:
        """Read iteration count + measurements off the main thread,
        then deliver the UI update via ``invoke_in_main_thread``.

        Safe to call from any thread (subscription executor, panel
        refresh QThreadFuture, etc.).
        """
        if self._adaptive is None or not self._field_name:
            return

        from lucid.utils.threads import invoke_in_main_thread

        old_count = self._n_iterations

        with log_time("_fetch_new_iterations (worker): read .shape", level="DEBUG"):
            try:
                n = 0
                for field in _HEATMAP_FIELDS:
                    if field in self._adaptive:
                        n = self._adaptive[field].shape[0]
                        break
            except Exception:
                return

        if n == old_count:
            return  # Nothing new

        # Refresh measurement overlay data while still off main thread
        meas_x = meas_y = None
        try:
            start = self._run.metadata.get("start", {})
            dims = start.get("hints", {}).get("dimensions", [])
            x_field = dims[0][0][0]
            y_field = dims[1][0][0]
            primary = self._run["primary"]
            with log_time("_fetch_new_iterations (worker): read measurements", level="DEBUG"):
                meas_x = np.asarray(primary[x_field].read())
                meas_y = np.asarray(primary[y_field].read())
        except Exception:
            pass

        result = {"n": n, "meas_x": meas_x, "meas_y": meas_y}
        invoke_in_main_thread(self._apply_new_iterations, result)

    def _apply_new_iterations(self, result: dict) -> None:
        """Main-thread callback: update the display with new iteration data."""
        n = result["n"]
        old_count = self._n_iterations

        if n == old_count:
            return  # Stale by the time we got here

        was_at_end = (
            self._current_index == self._n_iterations - 1
            if self._n_iterations
            else True
        )

        self._n_iterations = n
        logger.debug("AdaptiveHeatmap: {} → {} iterations", old_count, n)

        # Apply cached measurement data from the worker
        if result.get("meas_x") is not None:
            self._meas_x = result["meas_x"]
            self._meas_y = result["meas_y"]

        timestamps = np.arange(n, dtype=np.float64)
        self._image_view.updateFrameCount(n, timestamps)

        if was_at_end:
            new_index = n - 1
            self._image_view.setCurrentIndex(new_index)
            self._current_index = new_index
            self._apply_grid_rect()
            self._update_overlays()

        self._update_status()

    # ------------------------------------------------------------------
    # Iteration / overlay callbacks
    # ------------------------------------------------------------------

    def _on_iteration_changed(self, ind: int, _time: float) -> None:
        """Called when the user scrubs the timeline.

        pyqtgraph emits ``sigTimeChanged`` on every mouse-move during a
        drag, not just when the discrete index changes.  Guard against
        redundant work (thread spawns, scatter re-renders) when the
        iteration hasn't actually changed.
        """
        if ind == self._current_index:
            return
        self._current_index = ind
        self._update_target_overlay_async()
        self._update_measurement_overlay()
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
        self._update_target_overlay_async()
        self._update_measurement_overlay()

    def _update_target_overlay_async(self) -> None:
        """Fetch target positions for the current iteration off the main thread."""
        if self._target_scatter is None:
            return
        if not self._target_check.isChecked():
            self._target_scatter.clear()
            return
        if self._adaptive is None or self._current_index < 0:
            self._target_scatter.clear()
            return
        if "targets" not in self._adaptive:
            self._target_scatter.clear()
            return

        from lucid.utils.threads import QThreadFuture
        from lucid.utils.tiled_helpers import fetch_frame

        idx = self._current_index
        targets_client = self._adaptive["targets"]
        # target_shape is recorded by tsuchinoko's TiledPublisher in the
        # data_keys metadata (parallels grid_shape for posterior arrays).
        # Falls back to (-1, 2) for legacy runs that stored raveled pairs.
        target_shape = self._target_logical_shape()

        def do_fetch() -> np.ndarray:
            return fetch_frame(targets_client, idx)

        def on_result(targets: np.ndarray) -> None:
            if idx != self._current_index:
                return  # Stale
            try:
                self._apply_target_scatter(targets, target_shape)
            except RuntimeError:
                pass

        future = QThreadFuture(
            do_fetch,
            callback_slot=on_result,
            except_slot=lambda e: logger.debug(
                "Target overlay fetch failed: {}", e,
            ),
            register=False,
            name=f"targets-{idx}",
        )
        self._image_view._in_flight.add(future)
        future.finished.connect(lambda f=future: self._image_view._in_flight.discard(f))
        future.start()

    def _target_logical_shape(self) -> tuple[int, int] | None:
        """Return (N_max, D) from descriptor metadata, or None for legacy runs."""
        if self._adaptive is None:
            return None
        try:
            dk = self._adaptive.metadata.get("data_keys", {})
            shape = dk.get("targets", {}).get("target_shape")
            if shape and len(shape) == 2:
                return (int(shape[0]), int(shape[1]))
        except Exception:
            pass
        return None

    def _apply_target_scatter(
        self,
        targets: np.ndarray,
        target_shape: tuple[int, int] | None,
    ) -> None:
        """Apply fetched target data to the scatter plot (main thread).

        The current writer stores a flat ``N_max * D`` vector per
        iteration with unused rows NaN-padded; ``target_shape`` from
        the descriptor's data_keys lets us reshape and drop padding.
        Legacy raveled runs without ``target_shape`` are reshaped as
        ``(-1, 2)`` since this viz is 2D-only.
        """
        if targets is None or targets.size == 0:
            self._target_scatter.clear()
            return

        if target_shape is not None:
            try:
                arr = np.asarray(targets, dtype=float).reshape(target_shape)
            except ValueError:
                self._target_scatter.clear()
                return
        elif targets.ndim == 2 and targets.shape[1] >= 2:
            arr = targets
        elif targets.ndim == 1 and len(targets) >= 2 and len(targets) % 2 == 0:
            arr = targets.reshape(-1, 2)
        else:
            self._target_scatter.clear()
            return

        valid = ~np.isnan(arr[:, :2]).any(axis=1)
        if not valid.any():
            self._target_scatter.clear()
            return
        xs = arr[valid, 0]
        ys = arr[valid, 1]
        self._target_scatter.setData(x=xs, y=ys)

    def _refresh_measurement_cache(self) -> None:
        """Fetch measurement overlay data from the primary stream.

        Called once on ``set_stream`` and again on each poll tick that
        discovers new iterations — NOT on every scrub position.
        """
        self._meas_x = self._meas_y = None
        if self._run is None:
            return

        try:
            start = self._run.metadata.get("start", {})
            dims = start.get("hints", {}).get("dimensions", [])
            x_field = dims[0][0][0]
            y_field = dims[1][0][0]
        except (IndexError, KeyError, TypeError):
            return

        try:
            primary = self._run["primary"]
            self._meas_x = np.asarray(primary[x_field].read())
            self._meas_y = np.asarray(primary[y_field].read())
        except Exception:
            self._meas_x = self._meas_y = None

    def _update_measurement_overlay(self) -> None:
        """Draw cached measured points (no HTTP calls)."""
        if self._meas_scatter is None or not self._meas_check.isChecked():
            if self._meas_scatter is not None:
                self._meas_scatter.clear()
            return
        if self._meas_x is not None and len(self._meas_x) > 0:
            self._meas_scatter.setData(x=self._meas_x, y=self._meas_y)
        else:
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
        self._stop_subscription()
        super().closeEvent(event)
