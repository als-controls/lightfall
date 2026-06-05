"""Lazy image viewer backed by tiled ArrayClient.

Subclasses pyqtgraph's ImageView to fetch frames on demand via
``ArrayClient[i]`` instead of holding the full stack in memory.
Follows the Xi-CAM XArrayView pattern (imageviewmixins.py:172).

Log intensity display follows the same design as
:class:`~lightfall.ui.widgets.camera.image_view.OphydImageView`:

- The histogram always operates in **real (linear) intensity units**.
- When log mode is on, ``log1p(frame)`` is displayed on the ImageItem.
- Histogram levels are mapped through ``log1p`` before being applied
  to the ImageItem, so the user adjusts contrast in real units.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtCore import Signal


class _ArrayProxy:
    """Lightweight proxy providing the attributes pyqtgraph's ``setImage``
    requires (dtype, max, min, ndim, shape, size) without loading the
    full data stack.

    pyqtgraph ImageView.setImage checks::

        required = ['dtype', 'max', 'min', 'ndim', 'shape', 'size']

    We satisfy these from metadata alone.
    """

    def __init__(
        self,
        n_frames: int,
        frame_shape: tuple[int, ...],
        dtype: np.dtype,
    ) -> None:
        self.shape = (n_frames, *frame_shape)
        self.ndim = len(self.shape)
        self.dtype = dtype
        self.size = int(np.prod(self.shape))
        # Dummy extremes — LazyImageView overrides quickMinMax so these
        # are only used as a fallback.
        self._min = 0.0
        self._max = 1.0

    # pyqtgraph calls data.min() / data.max() on the proxy via
    # quickMinMax → nanmin / nanmax, which eventually call .min()/.max().
    def min(self) -> float:
        return self._min

    def max(self) -> float:
        return self._max

    def set_extremes(self, lo: float, hi: float) -> None:
        self._min = lo
        self._max = hi

    def update_frame_count(self, n_frames: int) -> None:
        """Update the proxy when new frames arrive (live run)."""
        self.shape = (n_frames, *self.shape[1:])
        self.size = int(np.prod(self.shape))


class LazyImageView(pg.ImageView):
    """ImageView that fetches one frame at a time from a tiled ArrayClient.

    Instead of passing a full numpy stack to ``setImage``, call
    ``setArraySource`` with a tiled ``ArrayClient``.  Scrubbing the
    timeline triggers a single HTTP fetch for the displayed frame.
    """

    #: Emitted ``(index, message)`` when a frame fetch fails. Frame loads
    #: fail for *recoverable* reasons — the Tiled server being unreachable,
    #: the backing detector file not yet flushed, a partial/corrupt asset.
    #: Hosts connect this to surface a message instead of letting the
    #: exception propagate into the open-run action (which previously
    #: escalated to an unhandled-exception crash).
    frameLoadFailed = Signal(int, str)

    #: Emitted when a frame is successfully fetched and displayed. Lets the
    #: host clear any prior :attr:`frameLoadFailed` state — clearing must be
    #: driven by a real success, not by ``sigTimeChanged`` (which fires for a
    #: failed load too), so the error message survives an initial bad load.
    frameLoaded = Signal()

    def __init__(self, parent: Any | None = None, **kwargs: Any) -> None:
        # PlotItem provides axes (ticks + labels) around the image,
        # matching the Camera Control OphydImageView pattern.
        view = pg.PlotItem()
        view.setDefaultPadding(0)
        view.hideButtons()
        view.setMenuEnabled(False)
        view.getViewBox().setAspectLocked(True)
        view.setLabel("bottom", "x (px)")
        view.setLabel("left", "y (px)")

        super().__init__(parent=parent, view=view, **kwargs)

        # Col-major axis order (Xi-CAM convention), matching OphydImageView.
        self.imageItem.setOpts(axisOrder="col-major")

        self._client: Any | None = None
        self._frame_shape: tuple[int, ...] = ()
        self._proxy: _ArrayProxy | None = None
        self._fetch_func: Any | None = None  # Optional custom frame fetcher
        self._minmax_cache: list[tuple[float, float]] | None = None
        self._log_mode: bool = False
        self._dark_frame: np.ndarray | None = None
        self._last_real_frame: np.ndarray | None = None
        self._applying_log_levels: bool = False
        self._suppress_update: bool = False  # Skip frame fetch during setImage setup

        # Async frame loading — first frame after setArraySource is
        # synchronous (so the host can call _on_reset_lut immediately),
        # then scrubbing switches to background QThreadFuture fetches.
        self._first_frame_loaded: bool = False
        self._fetch_gen: int = 0
        # Set of in-flight futures — prevents GC from tearing down signal
        # connections before results are delivered.  Each future removes
        # itself from the set when its ``finished`` signal fires.
        self._in_flight: set[Any] = set()

        # Intercept histogram level changes so we can map through log1p
        # when log mode is active.
        self.ui.histogram.sigLevelsChanged.connect(self._on_hist_levels_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setArraySource(
        self,
        client: Any,
        timestamps: np.ndarray,
        frame_shape: tuple[int, ...],
        *,
        fetch_func: Any | None = None,
    ) -> None:
        """Configure the view with a lazy ArrayClient source.

        Args:
            client: Tiled ``ArrayClient`` with shape ``(N, ...)``.
                ``client[i]`` returns a flat or shaped numpy array for
                frame *i*.  May be a lightweight stub (only ``.shape`` is
                required) when *fetch_func* is provided.
            timestamps: 1-D array of epoch timestamps, length N.
            frame_shape: ``(H, W)`` shape of each frame (from descriptor
                ``data_keys[field]["shape"]``).
            fetch_func: Optional callable ``(index: int) -> np.ndarray``
                that returns a single 2-D frame.  When given, this is
                used instead of the default tiled ``fetch_frame`` helper,
                allowing non-tiled data sources (e.g. zarr iteration
                arrays) to plug into :class:`LazyImageView`.
        """
        self._client = client
        self._frame_shape = frame_shape
        self._fetch_func = fetch_func
        self._first_frame_loaded = False
        n_frames = len(timestamps) if len(timestamps) > 0 else client.shape[0]

        # Assume float64 — _fetch_frame converts anyway. Avoids an HTTP
        # round-trip just to discover dtype.
        self._proxy = _ArrayProxy(n_frames, frame_shape, np.dtype("float64"))
        self._minmax_cache = None

        # Relative timestamps (start at 0)
        t0 = timestamps[0] if len(timestamps) > 0 else 0.0
        self.tVals = np.asarray(timestamps[:n_frames], dtype=np.float64) - t0

        # Feed the proxy as the "image". Suppress the updateImage call
        # that pyqtgraph triggers internally — we don't want to fetch a
        # frame yet.  The caller will call setCurrentIndex to display
        # the desired frame (single HTTP fetch).
        self._suppress_update = True
        self.setImage(
            self._proxy,
            xvals=self.tVals,
            # Col-major: frame axes are (x, y), not (y, x).
            axes={"t": 0, "x": 1, "y": 2},
            autoLevels=False,
            autoRange=False,
        )
        self._suppress_update = False

        logger.debug(
            "LazyImageView: {} frames, shape {}",
            n_frames,
            frame_shape,
        )

    def updateFrameCount(self, new_count: int, timestamps: np.ndarray) -> None:
        """Update frame count when new data arrives during a live run.

        Args:
            new_count: New total number of frames.
            timestamps: Updated full timestamp array.
        """
        if self._proxy is None:
            return

        self._proxy.update_frame_count(new_count)
        t0 = timestamps[0] if len(timestamps) > 0 else 0.0
        self.tVals = np.asarray(timestamps[:new_count], dtype=np.float64) - t0

        # Update timeline bounds
        if len(self.tVals) > 1:
            start = self.tVals.min()
            stop = self.tVals.max() + abs(self.tVals[-1] - self.tVals[0]) * 0.02
        elif len(self.tVals) == 1:
            start = self.tVals[0] - 0.5
            stop = self.tVals[0] + 0.5
        else:
            start = 0
            stop = 1

        self.ui.roiPlot.setXRange(float(start), float(stop))
        self.frameTicks.setXVals(self.tVals)
        for s in [self.timeLine, self.normRgn]:
            s.setBounds([float(start), float(stop)])

        # Invalidate min/max cache since new data may extend range
        self._minmax_cache = None

    # ------------------------------------------------------------------
    # Log / BG-correct controls
    # ------------------------------------------------------------------

    def set_log_mode(self, enabled: bool) -> None:
        """Toggle log intensity mode.

        The histogram stays in real units; only the displayed image
        and the level mapping change.
        """
        self._log_mode = enabled
        self._minmax_cache = None
        self.updateImage()

    def set_dark_frame(self, dark: np.ndarray | None) -> None:
        """Set or clear the dark frame for background correction."""
        self._dark_frame = dark
        self._minmax_cache = None
        if self._client is not None and self._proxy is not None:
            self.updateImage()

    def _bg_correct_frame(self, frame: np.ndarray) -> np.ndarray:
        """Apply background correction (dark subtraction) only."""
        if self._dark_frame is not None and self._dark_frame.shape == frame.shape:
            frame = frame - self._dark_frame
            np.clip(frame, 0, None, out=frame)
        return frame

    # ------------------------------------------------------------------
    # Histogram / level helpers (log-aware)
    # ------------------------------------------------------------------

    def _set_hist_from_real(
        self, real_frame: np.ndarray, auto_range: bool = True
    ) -> None:
        """Set histogram bins and range from real-unit frame data.

        This ensures the histogram always shows the distribution in
        linear intensity units, regardless of log display mode.
        """
        from lightfall.utils.logging import log_time

        with log_time("  _set_hist_from_real: nanmin/nanmax", level="DEBUG"):
            lo = float(np.nanmin(real_frame))
            hi = float(np.nanmax(real_frame))
        self._imageLevels = [(lo, hi)]
        self.levelMin = lo
        self.levelMax = hi

        if auto_range:
            with log_time("  _set_hist_from_real: setHistogramRange", level="DEBUG"):
                self.ui.histogram.setHistogramRange(lo, hi)

        # Manually set histogram bins from real data
        with log_time("  _set_hist_from_real: np.histogram", level="DEBUG"):
            step = max(1, real_frame.size // 500_000)
            vals = real_frame.ravel()[::step].astype(np.float64)
            # Explicit range avoids ValueError when all values are identical
            hist_range = (lo, hi) if lo < hi else (lo - 0.5, lo + 0.5)
            hist_counts, hist_edges = np.histogram(vals, bins=256, range=hist_range)
            hist_centers = (hist_edges[:-1] + hist_edges[1:]) / 2

        with log_time("  _set_hist_from_real: plot.setData", level="DEBUG"):
            self.ui.histogram.item.plot.setData(hist_centers, hist_counts)

    def _set_hist_bins(self, real_frame: np.ndarray) -> None:
        """Set only the histogram bins (not range/levels) from real data."""
        step = max(1, real_frame.size // 500_000)
        vals = real_frame.ravel()[::step].astype(np.float64)
        lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
        hist_range = (lo, hi) if lo < hi else (lo - 0.5, lo + 0.5)
        hist_counts, hist_edges = np.histogram(vals, bins=256, range=hist_range)
        hist_centers = (hist_edges[:-1] + hist_edges[1:]) / 2
        self.ui.histogram.item.plot.setData(hist_centers, hist_counts)

    def _apply_log_levels(self) -> None:
        """Map histogram levels through log1p and apply to the ImageItem.

        Uses ``setLevels(update=False)`` so that no ``sigImageChanged``
        is emitted and the histogram bins are not recomputed from log data.
        """
        if not self._log_mode or self._applying_log_levels:
            return
        self._applying_log_levels = True
        try:
            lo, hi = self.ui.histogram.getLevels()
            mapped_lo = np.log1p(max(float(lo), 0.0))
            mapped_hi = np.log1p(max(float(hi), 0.0))
            self.imageItem.setLevels([mapped_lo, mapped_hi], update=False)
            self.imageItem.qimage = None  # force re-render with new levels
            self.imageItem.update()
        finally:
            self._applying_log_levels = False

    def _on_hist_levels_changed(self) -> None:
        """Intercept histogram level changes for log-mode level mapping.

        When the user drags the histogram sliders, pyqtgraph applies the
        real-unit levels directly to the ImageItem (wrong for log data).
        We correct the histogram bins (which pyqtgraph auto-recomputed
        from the log-data ImageItem) and re-apply log-mapped levels.
        """
        if not self._log_mode or self._applying_log_levels:
            return
        from lightfall.utils.logging import log_time

        with log_time("_on_hist_levels_changed (reactive)", level="DEBUG"):
            # pyqtgraph's regionChanged already called imageItem.setLevels
            # with real-unit levels AND triggered imageChanged which
            # recomputed histogram bins from log data.  Fix both:
            if self._last_real_frame is not None:
                self._set_hist_bins(self._last_real_frame)
            self._apply_log_levels()

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def updateImage(self, autoHistogramRange: bool = True) -> None:
        """Fetch and display the single frame at ``self.currentIndex``.

        Overrides the base ``ImageView.updateImage`` to:
        - Avoid ``getProcessedImage`` which would normalize the full stack.
        - Keep the histogram in real units when log mode is active.
        - Apply log display + level mapping when needed.

        The first call after :meth:`setArraySource` fetches synchronously
        (so the host can immediately call ``_on_reset_lut``).  Subsequent
        calls (timeline scrubbing) fetch in a background
        :class:`QThreadFuture` to keep the UI responsive.
        """
        if self._suppress_update:
            return

        if self._client is not None and self._proxy is not None:
            # === Lazy path ===
            if self._first_frame_loaded:
                self._update_image_async(autoHistogramRange)
            else:
                self._update_image_sync(autoHistogramRange)
                self._first_frame_loaded = True

        elif self.image is not None:
            # === Eager path (in-memory stack via setImage) ===
            # Let base class select the frame and display it.
            super().updateImage(autoHistogramRange)

            if self._log_mode:
                # The ImageItem now has a real-unit frame from the stack.
                real_img = self.imageItem.image
                if real_img is not None:
                    self._last_real_frame = real_img.copy()

                    # Override display with log-transformed frame
                    self.imageItem.updateImage(
                        np.log1p(real_img.astype(np.float64))
                    )
                    # Correct histogram bins (auto-update set them from log data)
                    self._set_hist_from_real(
                        self._last_real_frame,
                        auto_range=autoHistogramRange,
                    )
                    # Apply log-mapped levels
                    self._apply_log_levels()

    # ------------------------------------------------------------------
    # Sync / async frame loading
    # ------------------------------------------------------------------

    def _update_image_sync(self, autoHistogramRange: bool) -> None:
        """Fetch and apply the current frame on the calling thread.

        A fetch failure here is recoverable (server down, file not ready) and
        must not propagate: this runs inside ``updateImage`` → ``setCurrentIndex``
        during ``set_field``, so an exception would crash the whole open-run
        action. Log it, signal the host, and leave the view empty instead.
        """
        from lightfall.utils.logging import log_time

        try:
            with log_time("_update_image_sync: _fetch_frame (main thread!)", level="DEBUG"):
                raw_frame = self._fetch_frame(self.currentIndex)
        except Exception as exc:
            logger.warning(
                "LazyImageView: failed to load frame {}: {}", self.currentIndex, exc
            )
            self.frameLoadFailed.emit(int(self.currentIndex), str(exc))
            return
        self._apply_fetched_frame(raw_frame, autoHistogramRange)

    def _update_image_async(self, autoHistogramRange: bool) -> None:
        """Kick off a background fetch; apply on the main thread when done."""
        from lightfall.utils.logging import log_time
        from lightfall.utils.threads import QThreadFuture

        self._fetch_gen += 1
        gen = self._fetch_gen
        index = self.currentIndex

        def do_fetch() -> np.ndarray:
            with log_time(f"do_fetch[{index}]: _fetch_frame (worker thread)", level="DEBUG"):
                frame = self._fetch_frame(index)
            with log_time(f"do_fetch[{index}]: ascontiguousarray (worker thread)", level="DEBUG"):
                resolved = np.ascontiguousarray(frame)
            return resolved

        def on_result(raw_frame: np.ndarray) -> None:
            if gen != self._fetch_gen:
                logger.debug("Stale frame {} discarded (gen {} != {})", index, gen, self._fetch_gen)
                return
            try:
                self._apply_fetched_frame(raw_frame, autoHistogramRange)
            except RuntimeError:
                pass  # Widget destroyed while fetch was in flight

        with log_time("_update_image_async: QThreadFuture setup", level="DEBUG"):
            future = QThreadFuture(
                do_fetch,
                callback_slot=on_result,
                except_slot=lambda e: self._on_async_fetch_failed(index, e),
                register=False,  # avoid blocking cancel-on-key during scrubbing
                name=f"frame-{index}",
            )

        # Keep the future alive until it finishes — prevents Python's
        # refcount GC from destroying the object (and tearing down the
        # sigResult connection) before the result is delivered.
        self._in_flight.add(future)
        future.finished.connect(lambda f=future: self._in_flight.discard(f))

        future.start()

    def _on_async_fetch_failed(self, index: int, exc: Exception) -> None:
        """Handle a background frame-fetch failure (timeline scrubbing).

        Same recoverable failures as the sync path; surface them via the same
        signal so the host shows one consistent message.
        """
        logger.warning(
            "LazyImageView: async fetch for frame {} failed: {}", index, exc
        )
        self.frameLoadFailed.emit(int(index), str(exc))

    def _apply_fetched_frame(
        self, raw_frame: np.ndarray, autoHistogramRange: bool
    ) -> None:
        """Process and display a fetched frame (called from either thread path)."""
        from lightfall.utils.logging import log_time

        with log_time("_apply_fetched_frame: bg_correct", level="DEBUG"):
            real_frame = self._bg_correct_frame(raw_frame)
        self._last_real_frame = real_frame

        with log_time("_apply_fetched_frame: log1p / copy", level="DEBUG"):
            if self._log_mode:
                display_frame = np.log1p(real_frame)
            else:
                display_frame = real_frame

        # Guard: pyqtgraph requires a 2D array
        if display_frame.ndim != 2:
            logger.warning(
                "LazyImageView: frame has unexpected shape {}, skipping",
                display_frame.shape,
            )
            return

        self.imageDisp = display_frame

        # Display frame on ImageItem (may trigger histogram auto-update
        # with wrong bins if log is on — corrected immediately below).
        with log_time("_apply_fetched_frame: imageItem.updateImage", level="DEBUG"):
            self.imageItem.updateImage(display_frame)

        # Always set histogram from real data (overwrites any auto-update)
        with log_time("_apply_fetched_frame: _set_hist_from_real", level="DEBUG"):
            self._set_hist_from_real(real_frame, auto_range=autoHistogramRange)

        # Apply log-mapped levels
        with log_time("_apply_fetched_frame: _apply_log_levels", level="DEBUG"):
            self._apply_log_levels()

        # A frame displayed successfully — let the host clear any error state.
        self.frameLoaded.emit()

    def getProcessedImage(self) -> np.ndarray:
        """Return the current single frame (skip normalisation).

        Lazy path: returns the BG-corrected (real-unit) frame.
        Eager path: delegates to base class for normal stack processing.
        """
        from lightfall.utils.logging import log_time

        if self._client is not None:
            with log_time("getProcessedImage: _fetch_frame (main thread!)", level="DEBUG"):
                raw_frame = self._fetch_frame(self.currentIndex)
            real_frame = self._bg_correct_frame(raw_frame)
            # Cache levels from real data
            self._imageLevels = self.quickMinMax(real_frame)
            self.levelMin = min(lv[0] for lv in self._imageLevels)
            self.levelMax = max(lv[1] for lv in self._imageLevels)
            self.imageDisp = real_frame
            return real_frame
        else:
            return super().getProcessedImage()

    def quickMinMax(self, data: Any) -> list[tuple[float, float]]:
        """Estimate min/max by subsampling ~10 evenly-spaced frames.

        Follows the Xi-CAM pattern (imageviewmixins.py:265).  If *data*
        is a plain ndarray (single frame passed by getProcessedImage),
        just compute directly.

        Always returns real-unit (not log-transformed) extremes.
        """
        from lightfall.utils.logging import log_time

        # If called with a real numpy array (single frame), compute directly
        if isinstance(data, np.ndarray):
            if data.size == 0:
                return [(0.0, 0.0)]
            return [(float(np.nanmin(data)), float(np.nanmax(data)))]

        # For the proxy object, use cached subsampled estimate
        if self._minmax_cache is not None:
            return self._minmax_cache

        if self._client is None:
            return [(0.0, 1.0)]

        n_frames = self._client.shape[0]
        n_samples = min(10, n_frames)
        if n_samples == 0:
            return [(0.0, 1.0)]

        indices = np.linspace(0, n_frames - 1, n_samples, dtype=int)
        global_min = np.inf
        global_max = -np.inf

        with log_time(f"quickMinMax: fetching {n_samples} frames", level="DEBUG"):
            for idx in indices:
                frame = self._bg_correct_frame(self._fetch_frame(int(idx)))
                lo = float(np.nanmin(frame))
                hi = float(np.nanmax(frame))
                if lo < global_min:
                    global_min = lo
                if hi > global_max:
                    global_max = hi

        result = [(global_min, global_max)]
        self._minmax_cache = result

        # Also update the proxy extremes so .min()/.max() return sane values
        if self._proxy is not None:
            self._proxy.set_extremes(global_min, global_max)

        return result

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def frame_count(self) -> int:
        """Number of frames (or iterations) in the current source."""
        if self._proxy is not None:
            return self._proxy.shape[0]
        return 0

    def fetch_frame(self, index: int) -> np.ndarray:
        """Public API: fetch a single 2-D frame by index."""
        return self._fetch_frame(index)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_frame(self, index: int) -> np.ndarray:
        """Fetch a single frame via server-side slicing or custom fetcher.

        If the stored array is flattened (e.g. shape ``(N, H*W)`` instead
        of ``(N, H, W)``), the returned 1-D slice is reshaped to the
        expected ``_frame_shape`` so downstream code always gets a 2-D
        array.
        """
        if self._fetch_func is not None:
            frame = self._fetch_func(index).astype(np.float64)
        else:
            from lightfall.utils.tiled_helpers import fetch_frame
            frame = fetch_frame(self._client, index).astype(np.float64)

        # Reshape flattened frames using the known frame shape from metadata
        if (
            frame.ndim == 1
            and len(self._frame_shape) == 2
            and frame.size == self._frame_shape[0] * self._frame_shape[1]
        ):
            frame = frame.reshape(self._frame_shape)

        return frame
