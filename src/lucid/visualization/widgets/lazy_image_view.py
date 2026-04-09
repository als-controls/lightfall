"""Lazy image viewer backed by tiled ArrayClient.

Subclasses pyqtgraph's ImageView to fetch frames on demand via
``ArrayClient[i]`` instead of holding the full stack in memory.
Follows the Xi-CAM XArrayView pattern (imageviewmixins.py:172).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger


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

    def __init__(self, parent: Any | None = None, **kwargs: Any) -> None:
        super().__init__(parent=parent, **kwargs)
        self._client: Any | None = None
        self._frame_shape: tuple[int, ...] = ()
        self._proxy: _ArrayProxy | None = None
        self._minmax_cache: list[tuple[float, float]] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setArraySource(
        self,
        client: Any,
        timestamps: np.ndarray,
        frame_shape: tuple[int, ...],
    ) -> None:
        """Configure the view with a lazy ArrayClient source.

        Args:
            client: Tiled ``ArrayClient`` with shape ``(N, ...)``.
                ``client[i]`` returns a flat or shaped numpy array for
                frame *i*.
            timestamps: 1-D array of epoch timestamps, length N.
            frame_shape: ``(H, W)`` shape of each frame (from descriptor
                ``data_keys[field]["shape"]``).
        """
        self._client = client
        self._frame_shape = frame_shape
        n_frames = client.shape[0]

        # Infer dtype from the first frame (cheap — one HTTP request)
        sample = self._fetch_frame(0)
        dtype = sample.dtype

        # Build proxy and seed min/max from the sample frame
        self._proxy = _ArrayProxy(n_frames, frame_shape, dtype)
        self._minmax_cache = None  # will be computed lazily

        # Relative timestamps (start at 0)
        t0 = timestamps[0] if len(timestamps) > 0 else 0.0
        self.tVals = np.asarray(timestamps[:n_frames], dtype=np.float64) - t0

        # Feed the proxy as the "image" — pyqtgraph stores it as
        # self.image but never indexes into it because we override
        # updateImage / getProcessedImage / quickMinMax.
        self.setImage(
            self._proxy,
            xvals=self.tVals,
            axes={"t": 0, "y": 1, "x": 2},
            autoLevels=True,
            autoRange=True,
        )

        logger.debug(
            "LazyImageView: {} frames, shape {}, dtype {}",
            n_frames,
            frame_shape,
            dtype,
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
    # Overrides
    # ------------------------------------------------------------------

    def updateImage(self, autoHistogramRange: bool = True) -> None:
        """Fetch and display the single frame at ``self.currentIndex``.

        Overrides the base ``ImageView.updateImage`` to avoid
        ``getProcessedImage`` which would try to normalize the full stack.
        """
        if self._client is None or self._proxy is None:
            return

        frame = self._fetch_frame(self.currentIndex)

        # Set _imageLevels so that autoLevels() (called by setImage after
        # this method) finds valid level data instead of None.
        lo, hi = float(np.nanmin(frame)), float(np.nanmax(frame))
        self._imageLevels = [(lo, hi)]
        self.levelMin = lo
        self.levelMax = hi
        self.imageDisp = frame

        if autoHistogramRange:
            self.ui.histogram.setHistogramRange(lo, hi)

        # ImageItem expects (row, col) for row-major axis order
        self.imageItem.updateImage(frame)

    def getProcessedImage(self) -> np.ndarray:
        """Return the current single frame (skip normalisation)."""
        if self._client is None:
            return np.zeros((1, 1), dtype=np.float32)
        frame = self._fetch_frame(self.currentIndex)
        # Cache levels for autoLevels
        self._imageLevels = self.quickMinMax(frame)
        self.levelMin = min(lv[0] for lv in self._imageLevels)
        self.levelMax = max(lv[1] for lv in self._imageLevels)
        self.imageDisp = frame
        return frame

    def quickMinMax(self, data: Any) -> list[tuple[float, float]]:
        """Estimate min/max by subsampling ~10 evenly-spaced frames.

        Follows the Xi-CAM pattern (imageviewmixins.py:265).  If *data*
        is a plain ndarray (single frame passed by getProcessedImage),
        just compute directly.
        """
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

        for idx in indices:
            frame = self._fetch_frame(int(idx))
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
    # Internal
    # ------------------------------------------------------------------

    def _fetch_frame(self, index: int) -> np.ndarray:
        """Fetch a single frame from the ArrayClient, reshaping if flat.

        Tiled may store images flattened (e.g. shape (65536,) for 256x256).
        We reshape using ``self._frame_shape`` from the descriptor.
        """
        n_frames = self._client.shape[0]
        index = int(max(0, min(index, n_frames - 1)))

        raw = np.asarray(self._client[index])

        # Reshape flat arrays using the descriptor's declared shape
        if raw.ndim == 1 and len(self._frame_shape) == 2:
            expected_size = self._frame_shape[0] * self._frame_shape[1]
            if raw.size == expected_size:
                raw = raw.reshape(self._frame_shape)
            else:
                # Best-effort square reshape
                side = int(np.sqrt(raw.size))
                if side * side == raw.size:
                    raw = raw.reshape(side, side)

        return raw.astype(np.float64)
