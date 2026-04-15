"""Image stack visualization on the new BaseVisualization ABC.

Reads 2D array data directly from a tiled BlueskyEventStream via
ArrayClient.  Replaces the buffer/eager code paths with a single
tiled-only path while keeping the same toolbar controls (colormap,
Reset LUT, Log Intensity, ROI, etc.).
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pyqtgraph as pg
import qtawesome as qta
from loguru import logger
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.visualization.base_visualization import BaseVisualization
from lucid.visualization.widgets.lazy_image_view import LazyImageView
from lucid.visualization.widgets.time_axis import HumanReadableTimeAxis


class ImageStackVisualization(BaseVisualization):
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

        # Tiled state
        self._stream: Any | None = None
        self._data_keys: dict[str, Any] = {}
        self._image_client: Any | None = None
        self._frame_shape: tuple[int, ...] = ()
        self._timestamps: np.ndarray = np.empty(0)

        # UI state
        self._log_mode: bool = False
        self._roi: pg.RectROI | None = None
        self._roi_curves: list[pg.PlotDataItem] = []

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

        # Reset LUT
        self._reset_lut_btn = QPushButton(
            qta.icon("mdi6.chart-histogram"), "Reset LUT",
        )
        self._reset_lut_btn.setFixedHeight(24)
        self._reset_lut_btn.clicked.connect(self._on_reset_lut)
        toolbar.addWidget(self._reset_lut_btn)

        # Reset Axes
        self._reset_axes_btn = QPushButton(
            qta.icon("mdi6.magnify"), "Reset Axes",
        )
        self._reset_axes_btn.setFixedHeight(24)
        self._reset_axes_btn.clicked.connect(self._on_reset_axes)
        toolbar.addWidget(self._reset_axes_btn)

        # Log Intensity (toggle)
        self._log_icon_off = qta.icon("mdi6.lightbulb")
        self._log_icon_on = qta.icon("mdi6.lightbulb-on-outline")
        self._log_intensity_btn = QPushButton(self._log_icon_off, "Log Intensity")
        self._log_intensity_btn.setFixedHeight(24)
        self._log_intensity_btn.setCheckable(True)
        self._log_intensity_btn.toggled.connect(self._on_log_intensity_toggled)
        toolbar.addWidget(self._log_intensity_btn)

        # ROI toggle
        self._roi_btn = QPushButton("ROI")
        self._roi_btn.setCheckable(True)
        self._roi_btn.setToolTip("Enable region of interest")
        self._roi_btn.toggled.connect(self._on_roi_toggled)
        toolbar.addWidget(self._roi_btn)

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
            self._on_reset_lut()
        t4 = _time.monotonic()

        logger.debug(
            "set_field timings: resolve={:.1f}s shape={:.1f}s setSource={:.1f}s display={:.1f}s",
            t1 - t0, t2 - t1, t3 - t2, t4 - t3,
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

    # ------------------------------------------------------------------
    # Toolbar callbacks
    # ------------------------------------------------------------------

    def _on_reset_lut(self) -> None:
        """1st/99th percentile auto-levels on the current frame."""
        if self._image_view is None:
            return
        img = self._image_view.imageItem.image
        if img is None:
            return
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            step = max(1, img.size // 1_000_000)
            data = img.ravel()[::step].astype(np.float64)
            if data.size == 0:
                return
            lo = float(np.nanpercentile(data, 1))
            hi = float(np.nanpercentile(data, 99))
            if lo >= hi:
                lo = float(np.nanmin(data))
                hi = float(np.nanmax(data))
            if lo == hi:
                hi = lo + 1.0
        self._image_view.setLevels(lo, hi)

    def _on_reset_axes(self) -> None:
        if self._image_view:
            self._image_view.getView().autoRange()

    def _on_log_intensity_toggled(self, checked: bool) -> None:
        self._log_mode = checked
        self._log_intensity_btn.setIcon(
            self._log_icon_on if checked else self._log_icon_off,
        )
        if self._image_view:
            self._image_view.set_log_mode(checked)

    def _on_roi_toggled(self, enabled: bool) -> None:
        if enabled:
            self._create_roi()
            if self._roi:
                self._roi.show()
        else:
            if self._roi:
                self._roi.hide()
            self._clear_roi_curves()

    def _on_time_changed(self, ind: int, time: float) -> None:
        self._update_status()

    # ------------------------------------------------------------------
    # ROI
    # ------------------------------------------------------------------

    def _create_roi(self) -> None:
        if self._roi is not None:
            return
        if not self._frame_shape or len(self._frame_shape) < 2:
            return

        height, width = self._frame_shape
        roi_w = width // 2
        roi_h = height // 2
        roi_x = width // 4
        roi_y = height // 4

        self._roi = pg.RectROI(
            [roi_x, roi_y],
            [roi_w, roi_h],
            pen=pg.mkPen("r", width=2),
        )
        self._roi.addScaleHandle([1, 1], [0, 0])
        self._roi.addScaleHandle([0, 0], [1, 1])
        self._roi.addScaleHandle([1, 0], [0, 1])
        self._roi.addScaleHandle([0, 1], [1, 0])

        self._image_view.addItem(self._roi)

    def _clear_roi_curves(self) -> None:
        for curve in self._roi_curves:
            self._image_view.ui.roiPlot.removeItem(curve)
        self._roi_curves.clear()

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
