"""Image stack visualization on the new BaseVisualization ABC.

Reads 2D array data directly from a tiled BlueskyEventStream via
ArrayClient.  Replaces the buffer/eager code paths with a single
tiled-only path while keeping the same toolbar controls (colormap,
Reset LUT, Log Intensity, ROI, etc.).
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
from lucid.visualization.widgets.image_view_toolbar import ImageViewToolbarMixin
from lucid.visualization.widgets.lazy_image_view import LazyImageView
from lucid.visualization.widgets.time_axis import HumanReadableTimeAxis


class ImageStackVisualization(ImageViewToolbarMixin, BaseVisualization):
    """Tiled-only image stack viewer.

    Displays a sequence of 2D detector images fetched lazily from a
    tiled BlueskyRun entry.  Each frame is pulled on demand via the
    underlying :class:`LazyImageView`.
    """

    viz_name = "image_stack"
    viz_display_name = "Image Stack"
    viz_icon = "images"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_image_view_toolbar_state()

        # Tiled state
        self._stream: Any | None = None
        self._data_keys: dict[str, Any] = {}
        self._image_client: Any | None = None
        self._frame_shape: tuple[int, ...] = ()
        self._timestamps: np.ndarray = np.empty(0)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar
        layout.addLayout(self._build_toolbar())

        # Image view (lazy, with timeline)
        self._image_view = LazyImageView()
        self._image_view.ui.roiPlot.show()
        self._image_view.ui.roiPlot.setMinimumHeight(80)
        self._image_view.ui.splitter.setSizes([400, 100])

        # Human-readable time axis on the timeline
        self._time_axis = HumanReadableTimeAxis(orientation="bottom")
        self._image_view.ui.roiPlot.setAxisItems({"bottom": self._time_axis})

        self._image_view.sigTimeChanged.connect(self._on_time_changed)

        # Style the scrubber bar
        timeline = self._image_view.timeLine
        timeline.setPen(pg.mkPen("y", width=3))
        timeline.setHoverPen(pg.mkPen("y", width=5))

        # Hide built-in ROI / menu buttons (we provide our own)
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()

        # Default colormap
        self._apply_colormap("viridis")

        layout.addWidget(self._image_view)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Colormap selector
        cmap_label = QLabel("Colormap:")
        self._cmap_combo = QComboBox()
        self._cmap_combo.addItems([
            "viridis", "plasma", "inferno", "magma", "gray", "hot",
        ])
        self._cmap_combo.currentTextChanged.connect(self._apply_colormap)
        toolbar.addWidget(cmap_label)
        toolbar.addWidget(self._cmap_combo)

        toolbar.addStretch()

        # Standard image-view buttons (Reset LUT, Reset Axes, Log, ROI)
        self._build_image_view_buttons(toolbar)

        return toolbar

    # ------------------------------------------------------------------
    # BaseVisualization interface
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Score 75 if primary stream has a field with shape >= 2D."""
        try:
            data_keys = run["primary"].metadata.get("data_keys", {})
        except Exception:
            return 0
        for dk in data_keys.values():
            if len(dk.get("shape", [])) >= 2:
                return 75
        return 0

    def set_run(self, run: Any) -> None:
        self._run = run

    def get_streams(self) -> list[str]:
        if self._run is None:
            return []
        names = list(self._run.keys())
        # Sort "primary" first
        if "primary" in names:
            names.remove("primary")
            names.insert(0, "primary")
        return names

    def set_stream(self, stream_name: str) -> None:
        import time as _time
        t0 = _time.monotonic()

        self._stream_name = stream_name
        self._stream = self._run[stream_name]
        t1 = _time.monotonic()

        self._data_keys = self._stream.metadata.get("data_keys", {})
        t2 = _time.monotonic()
        logger.debug("set_stream: access={:.1f}s metadata={:.1f}s", t1 - t0, t2 - t1)

        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        """Return fields sorted: hinted 2D+ first, other 2D+ next, rest last."""
        if not self._data_keys:
            return []

        hints = set()
        try:
            hints = set(
                self._stream.metadata.get("hints", {}).get("fields", [])
            )
        except Exception:
            pass

        hinted_2d: list[str] = []
        other_2d: list[str] = []
        rest: list[str] = []

        for name, dk in self._data_keys.items():
            shape = dk.get("shape", [])
            if len(shape) >= 2:
                if name in hints:
                    hinted_2d.append(name)
                else:
                    other_2d.append(name)
            else:
                rest.append(name)

        return hinted_2d + other_2d + rest

    def set_field(self, field_name: str) -> None:
        import time as _time
        t0 = _time.monotonic()

        self._field_name = field_name

        # 1. Resolve the ArrayClient
        image_client = None
        try:
            image_client = self._stream[field_name]
        except Exception:
            pass

        if image_client is None:
            try:
                image_client = self._stream["external"][field_name]
            except Exception:
                logger.warning(
                    "ImageStackVisualization: could not resolve ArrayClient "
                    "for field '{}' in stream '{}'",
                    field_name,
                    self._stream_name,
                )
                return

        t1 = _time.monotonic()
        self._image_client = image_client

        # 2. Cache shape (single HTTP call) to avoid repeated round-trips
        full_shape = image_client.shape  # e.g. (21, 1024, 1024)
        n_frames = full_shape[0]

        # Prefer the frame shape from data_keys metadata — the stored
        # array may be flattened (e.g. (N, H*W) instead of (N, H, W))
        # when data is written internally vs. from external files.
        dk = self._data_keys.get(field_name, {})
        dk_shape = dk.get("shape", [])
        if len(dk_shape) >= 2:
            self._frame_shape = tuple(dk_shape[-2:])
        else:
            self._frame_shape = tuple(full_shape[-2:])
        t2 = _time.monotonic()

        # 3. Synthetic timestamps (reading events table is too expensive)
        timestamps = np.arange(n_frames, dtype=np.float64)
        self._timestamps = timestamps

        # 4. Hand off to LazyImageView
        self._image_view.setArraySource(image_client, timestamps, self._frame_shape)
        t3 = _time.monotonic()

        if n_frames > 0:
            self._image_view.setCurrentIndex(n_frames - 1)
            t3b = _time.monotonic()
            self._on_reset_lut()
        else:
            t3b = t3
        t4 = _time.monotonic()

        logger.debug(
            "set_field timings: resolve={:.1f}s shape={:.1f}s setSource={:.1f}s "
            "setIndex={:.1f}s resetLUT={:.1f}s",
            t1 - t0, t2 - t1, t3 - t2, t3b - t3, t4 - t3b,
        )

        self._update_status()
        logger.debug(
            "ImageStackVisualization: field='{}', {} frames, shape {}",
            field_name,
            n_frames,
            self._frame_shape,
        )

    def refresh(self) -> None:
        """Poll for new frames (live runs)."""
        if self._image_client is None:
            return

        current_count = self._image_client.shape[0]
        known_count = len(self._timestamps)

        if current_count <= known_count:
            return

        # Extend synthetic timestamps (reading events table is too expensive)
        timestamps = np.arange(current_count, dtype=np.float64)
        self._timestamps = timestamps

        self._image_view.updateFrameCount(current_count, timestamps)

        # Jump to the latest frame
        self._image_view.setCurrentIndex(current_count - 1)
        self._update_status()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply_colormap(self, cmap_name: str) -> None:
        try:
            cmap = pg.colormap.get(cmap_name)
            if cmap and self._image_view:
                self._image_view.setColorMap(cmap)
        except Exception as exc:
            logger.debug("Could not apply colormap '{}': {}", cmap_name, exc)

    def _on_time_changed(self, ind: int, time: float) -> None:
        self._update_status()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        if self._image_client is not None:
            total = self._image_client.shape[0]
        else:
            total = 0

        current_idx = self._image_view.currentIndex if self._image_view else 0
        current = current_idx + 1 if total > 0 else 0

        tvals = getattr(self._image_view, "tVals", None)
        if tvals is not None and 0 <= current_idx < len(tvals):
            label = f"Frame {current}/{total} | Time: {tvals[current_idx]:.3f}s"
        else:
            label = f"Frame {current}/{total}"

        self._time_axis.setLabel(label)
