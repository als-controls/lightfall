"""Scientific image viewer for ophyd area detector devices.

Displays live image data with:
- Axis ticks via PlotItem
- Histogram/LUT control
- Correct orientation (col-major axis order, matching Xi-CAM convention)
- Background-threaded device polling and data preprocessing

Device I/O (array_data.get(), roi stats, progress counters) and numpy
preprocessing (reshape, BG subtraction, log transform, histogram) all
run on a background thread. The main thread only does Qt widget updates.

The LUT is auto-scaled on the first frame received using percentile bounds
(1st percentile as min, 99th percentile as max). Subsequent frames preserve
the user's manual LUT adjustments until Reset LUT is pressed.
"""

from __future__ import annotations

import threading
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.widgets.camera.dark_frames import DarkFrameManager
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    pass


@dataclass
class _FrameData:
    """Preprocessed frame data produced by the background thread."""

    raw_image: np.ndarray
    display_image: np.ndarray
    log_mode: bool  # whether display_image was log-transformed
    hist_x: np.ndarray | None = None
    hist_y: np.ndarray | None = None
    roi_stats: list[str] | None = None
    progress: tuple[int, int] | None = None  # (current, total) or None if idle


class OphydImageView(QWidget):
    """PyQtGraph-based scientific image viewer for ophyd area detectors.

    Device polling and data preprocessing run on a background thread.
    The main thread timer picks up the latest preprocessed frame and
    only does Qt widget updates.

    Image orientation uses col-major axis order (matching Xi-CAM convention)
    with no Y-axis inversion, so no QTransform or data preprocessing needed.
    """

    _STAT_FIELDS = (
        "min_value", "max_value", "mean_value", "total",
        "centroid_x", "centroid_y",
    )

    def __init__(self, ophyd_device: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device = ophyd_device
        self._first_frame = True
        self._log_mode = False
        self._bg_correct = False
        self._raw_image: np.ndarray | None = None
        self._updating_levels = False

        self._dark_manager = DarkFrameManager(
            device_name=ophyd_device.name if hasattr(ophyd_device, "name") else "unknown"
        )

        # Background thread state
        self._pending_frame: _FrameData | None = None
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()
        self._frame_count = 0

        self._setup_ui()
        self._start_updates()

    # =====================================================================
    # UI Setup
    # =====================================================================

    def _setup_ui(self) -> None:
        """Build the viewer layout: [toolbar | image+histogram | coords | progress]."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        import qtawesome as qta

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)

        self._reset_lut_btn = QPushButton(qta.icon("mdi6.chart-histogram"), "Reset LUT")
        self._reset_lut_btn.setFixedHeight(24)
        self._reset_lut_btn.clicked.connect(self.reset_lut)
        toolbar.addWidget(self._reset_lut_btn)

        self._reset_axes_btn = QPushButton(qta.icon("mdi6.magnify"), "Reset Axes")
        self._reset_axes_btn.setFixedHeight(24)
        self._reset_axes_btn.clicked.connect(self.reset_axes)
        toolbar.addWidget(self._reset_axes_btn)

        self._log_icon_off = qta.icon("mdi6.lightbulb")
        self._log_icon_on = qta.icon("mdi6.lightbulb-on-outline")
        self._log_intensity_btn = QPushButton(self._log_icon_off, "Log Intensity")
        self._log_intensity_btn.setFixedHeight(24)
        self._log_intensity_btn.setCheckable(True)
        self._log_intensity_btn.toggled.connect(self._on_log_intensity_toggled)
        toolbar.addWidget(self._log_intensity_btn)

        self._bg_icon_off = qta.icon("mdi6.lightbulb-night")
        self._bg_icon_on = qta.icon("mdi6.lightbulb-night-outline")
        self._bg_correct_btn = QPushButton(self._bg_icon_off, "BG Correct")
        self._bg_correct_btn.setFixedHeight(24)
        self._bg_correct_btn.setCheckable(True)
        self._bg_correct_btn.toggled.connect(self._on_bg_correct_toggled)
        toolbar.addWidget(self._bg_correct_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Main horizontal split: image view | histogram
        h_layout = QHBoxLayout()
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        # PlotItem provides axes around the image
        self._plot_item = pg.PlotItem()
        self._plot_item.setDefaultPadding(0)
        self._plot_item.hideButtons()
        self._plot_item.setMenuEnabled(False)
        self._plot_item.getViewBox().setAspectLocked(True)
        self._plot_item.setLabel("bottom", "x (px)")
        self._plot_item.setLabel("left", "y (px)")

        # ImageItem uses col-major axis order (Xi-CAM convention).
        self._image_item = pg.ImageItem()
        self._image_item.setOpts(axisOrder="col-major")
        self._plot_item.addItem(self._image_item)

        # ROI stats overlay
        self._stats_text = pg.TextItem(anchor=(1, 0), color="#00FF00")
        self._stats_text.setFont(pg.QtGui.QFont("monospace", 9))
        self._stats_text.setVisible(False)
        self._plot_item.addItem(self._stats_text)

        # Crosshair
        linepen = pg.mkPen("#FFA500", width=1)
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=linepen)
        self._hline = pg.InfiniteLine(angle=0, movable=False, pen=linepen)
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        self._plot_item.addItem(self._vline)
        self._plot_item.addItem(self._hline)

        # GraphicsView to host the PlotItem
        self._graphics_view = pg.GraphicsView()
        self._graphics_view.setCentralItem(self._plot_item)

        # Mouse tracking (must connect after PlotItem has a scene via GraphicsView)
        self._plot_item.scene().sigMouseMoved.connect(self._on_mouse_moved)
        h_layout.addWidget(self._graphics_view, stretch=1)

        # HistogramLUTItem — manually wired so we can decouple log display
        # from linear histogram.
        self._histogram = pg.HistogramLUTItem()
        self._histogram.sigLevelsChanged.connect(self._apply_display_levels)
        self._histogram.gradient.sigGradientChanged.connect(
            self._on_gradient_changed,
        )

        self._hist_view = pg.GraphicsView()
        self._hist_view.setCentralItem(self._histogram)
        self._hist_view.setFixedWidth(120)
        h_layout.addWidget(self._hist_view)

        layout.addLayout(h_layout)

        # Coordinate display label
        self._coords_label = QLabel("")
        self._coords_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._coords_label.setFixedHeight(20)
        self._coords_label.setStyleSheet("font-family: monospace; font-size: 11px;")
        layout.addWidget(self._coords_label)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(16)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v / %m  (%p%)")
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

    # =====================================================================
    # Background polling thread
    # =====================================================================

    def _start_updates(self) -> None:
        """Start the background polling thread and main-thread UI timer."""
        # Background thread: polls device, preprocesses data
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="imageview-poll"
        )
        self._poll_thread.start()

        # Main thread timer: picks up preprocessed frames, updates widgets
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._apply_frame)
        self._ui_timer.start(50)  # 20 fps check rate (only updates if new frame)

    def _poll_loop(self) -> None:
        """Background thread: poll device and preprocess frames.

        Runs continuously at ~10 fps. All device .get() calls and numpy
        operations happen here, never on the main thread.
        """
        while not self._poll_stop.is_set():
            try:
                self._poll_once()
            except Exception as e:
                logger.debug(f"Poll error: {e}")
            self._poll_stop.wait(0.1)  # ~10 fps

    def _poll_once(self) -> None:
        """Single poll iteration — read device, preprocess, store result."""
        if self._device is None:
            return

        # --- Read image data (blocking I/O) ---
        image_plugin = None
        for attr in ("image1", "image"):
            plugin = getattr(self._device, attr, None)
            if plugin is not None and hasattr(plugin, "array_data"):
                image_plugin = plugin
                break

        if image_plugin is None:
            return

        image_data = image_plugin.array_data.get()
        if image_data is None:
            return

        # --- Preprocess (all numpy, no Qt) ---
        arr = np.squeeze(image_data)

        if arr.ndim == 1:
            width, height = self._get_image_dimensions(image_plugin)
            if width and height and width * height == arr.size:
                arr = arr.reshape((height, width))
            else:
                return

        if arr.ndim != 2:
            return

        # Snapshot toggle states at the start of processing so the frame
        # is internally consistent even if the user toggles mid-poll.
        log_mode = self._log_mode
        bg_correct = self._bg_correct

        # Background correction (reads cached dark, no I/O)
        if bg_correct:
            arr = self._dark_manager.subtract(arr)

        # Log transform
        if log_mode:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                display_arr = np.log1p(arr.astype(np.float64))
        else:
            display_arr = arr

        # Histogram (throttled, subsampled)
        self._frame_count += 1
        hist_x, hist_y = None, None
        if self._frame_count % 5 == 1:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                step = max(1, arr.size // 500_000)
                vals = arr.ravel()[::step]
                hist_y_arr, hist_x_arr = np.histogram(vals, bins=256)
                hist_x = (hist_x_arr[:-1] + hist_x_arr[1:]) / 2
                hist_y = hist_y_arr

        # --- ROI stats (blocking I/O) ---
        roi_stats = self._read_roi_stats()

        # --- Progress (blocking I/O) ---
        progress = self._read_progress()

        # Store for main thread pickup
        self._pending_frame = _FrameData(
            raw_image=arr,
            display_image=display_arr,
            log_mode=log_mode,
            hist_x=hist_x,
            hist_y=hist_y,
            roi_stats=roi_stats,
            progress=progress,
        )

    def _read_roi_stats(self) -> list[str] | None:
        """Read ROI statistics from device (background thread)."""
        stats_plugin = getattr(self._device, "roi_stat1", None)
        if stats_plugin is None:
            return None
        try:
            lines = []
            for field_name in self._STAT_FIELDS:
                signal = getattr(stats_plugin, field_name, None)
                if signal is not None:
                    value = signal.get()
                    label = field_name.replace("_", " ").title()
                    if isinstance(value, float):
                        lines.append(f"{label}: {value:.1f}")
                    else:
                        lines.append(f"{label}: {value}")
            return lines if lines else None
        except Exception:
            return None

    def _read_progress(self) -> tuple[int, int] | None:
        """Read acquisition progress from device (background thread)."""
        cam = getattr(self._device, "cam", None)
        if cam is None:
            return None
        try:
            acquiring = getattr(cam, "acquire", None)
            if acquiring is None or not acquiring.get():
                return None

            # Try HDF5 plugin first
            hdf5 = getattr(self._device, "hdf5", None)
            if hdf5 is not None:
                capture = getattr(hdf5, "capture", None)
                if capture is not None and capture.get():
                    return int(hdf5.num_captured.get()), int(cam.num_images.get())

            # Fall back to cam.array_counter
            counter = getattr(cam, "array_counter", None)
            num_images = getattr(cam, "num_images", None)
            if counter is not None and num_images is not None:
                return int(counter.get()), int(num_images.get())
        except Exception:
            pass
        return None

    # =====================================================================
    # Main thread: apply preprocessed frame to widgets
    # =====================================================================

    def _apply_frame(self) -> None:
        """Main thread: pick up the latest preprocessed frame and update widgets."""
        frame = self._pending_frame
        if frame is None:
            return
        self._pending_frame = None

        # Cache raw image for coordinate readback
        self._raw_image = frame.raw_image

        # Update image display
        self._image_item.setImage(frame.display_image, autoLevels=False)

        # First frame: auto-levels and auto-range
        if self._first_frame:
            self._first_frame = False
            self._auto_levels()
            self.reset_axes()

        # Use the frame's log_mode (not current self._log_mode) so levels
        # match the data that was actually computed by the background thread.
        self._apply_display_levels(log_mode=frame.log_mode)

        # Update histogram bins
        if frame.hist_x is not None and frame.hist_y is not None:
            self._histogram.plot.setData(frame.hist_x, frame.hist_y)

        # Update ROI stats overlay
        if frame.roi_stats is not None:
            self._stats_text.setText("\n".join(frame.roi_stats))
            vb = self._plot_item.getViewBox()
            view_range = vb.viewRange()
            self._stats_text.setPos(view_range[0][1], view_range[1][0])
            self._stats_text.setVisible(True)
        else:
            self._stats_text.setVisible(False)

        # Update progress bar
        if frame.progress is not None:
            current, total = frame.progress
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
            self._progress_bar.setVisible(True)
        else:
            self._progress_bar.setVisible(False)

    def _display_array(self, array: np.ndarray, image_plugin: Any = None) -> None:
        """Synchronous display for testing and direct use.

        Preprocesses the array and updates widgets in one call on the
        current thread.  The normal runtime path uses the background
        poll thread instead.
        """
        if array is None or array.size == 0:
            return

        arr = np.squeeze(array)

        if arr.ndim == 1:
            width, height = self._get_image_dimensions(image_plugin)
            if width and height and width * height == arr.size:
                arr = arr.reshape((height, width))
            else:
                return

        if arr.ndim != 2:
            return

        if self._bg_correct:
            arr = self._dark_manager.subtract(arr)

        self._raw_image = arr

        if self._log_mode:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                display_arr = np.log1p(arr.astype(np.float64))
        else:
            display_arr = arr

        self._image_item.setImage(display_arr, autoLevels=False)

        if self._first_frame:
            self._first_frame = False
            self._auto_levels()
            self.reset_axes()

        self._apply_display_levels()

    # =====================================================================
    # LUT / Axes / Log / BG controls
    # =====================================================================

    def reset_lut(self) -> None:
        """Reset LUT using percentile-based bounds from current image."""
        if self._raw_image is not None:
            self._auto_levels()
            self._apply_display_levels()
        else:
            self._first_frame = True

    def reset_axes(self) -> None:
        """Reset view to fit the image bounds exactly."""
        if self._raw_image is not None:
            bounds = self._image_item.mapRectToView(
                self._image_item.boundingRect()
            )
            self._plot_item.getViewBox().setRange(bounds, padding=0.02)
        else:
            self._plot_item.getViewBox().autoRange()

    def _auto_levels(self) -> None:
        """Set histogram levels using percentile-based bounds.

        Uses 1st percentile as min and 99th percentile as max.
        This excludes hot pixels and dead pixels from dominating the
        color scale.
        """
        arr = self._raw_image
        if arr is None:
            return

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)

            # Subsample large images for speed
            step = max(1, arr.size // 1_000_000)
            data = arr.ravel()[::step].astype(np.float64)

            if data.size == 0:
                return

            lo = float(np.nanpercentile(data, 1))
            hi = float(np.nanpercentile(data, 99))

            if lo >= hi:
                lo = float(np.nanmin(data))
                hi = float(np.nanmax(data))
            if lo == hi:
                hi = lo + 1.0

            self._histogram.setLevels(lo, hi)

    def _on_log_intensity_toggled(self, checked: bool) -> None:
        """Toggle log intensity mode.

        The background thread reads this flag and applies the transform.
        Re-render the current cached frame immediately for responsiveness.
        """
        self._log_mode = checked
        self._log_intensity_btn.setIcon(self._log_icon_on if checked else self._log_icon_off)
        if self._raw_image is not None:
            if self._log_mode:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    display_arr = np.log1p(self._raw_image.astype(np.float64))
            else:
                display_arr = self._raw_image
            self._image_item.setImage(display_arr, autoLevels=False)
            self._apply_display_levels()

    def _on_bg_correct_toggled(self, checked: bool) -> None:
        """Toggle background correction. The background thread reads this flag."""
        self._bg_correct = checked
        self._bg_correct_btn.setIcon(self._bg_icon_on if checked else self._bg_icon_off)

    def _apply_display_levels(self, log_mode: bool | None = None) -> None:
        """Map histogram levels (real units) to the displayed ImageItem.

        Args:
            log_mode: Whether the currently displayed image is log-transformed.
                      If None, uses self._log_mode (for interactive updates).
                      When called from _apply_frame, pass the frame's log_mode
                      to ensure levels match the data.
        """
        if self._updating_levels:
            return
        self._updating_levels = True
        try:
            if log_mode is None:
                log_mode = self._log_mode
            lo, hi = self._histogram.getLevels()
            if log_mode:
                lo = np.log1p(max(lo, 0.0))
                hi = np.log1p(max(hi, 0.0))
            self._image_item.setLevels([lo, hi])
        finally:
            self._updating_levels = False

    def _on_gradient_changed(self) -> None:
        """Propagate colormap changes from the histogram to the ImageItem."""
        lut = self._histogram.gradient.getLookupTable(256)
        self._image_item.setLookupTable(lut)

    # =====================================================================
    # Crosshair / coordinates
    # =====================================================================

    def _on_mouse_moved(self, pos) -> None:
        vb = self._plot_item.getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            self._coords_label.setText("")
            return
        mouse_point = vb.mapSceneToView(pos)
        x, y = mouse_point.x(), mouse_point.y()
        text = self._format_coordinates(x, y)
        if text:
            self._vline.setPos(x)
            self._hline.setPos(y)
            self._vline.setVisible(True)
            self._hline.setVisible(True)
            self._coords_label.setText(text)
        else:
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            self._coords_label.setText("")

    def _format_coordinates(self, x: float, y: float) -> str:
        """Format pixel coordinates and intensity at view position (x, y).

        With col-major axis order, view (x, y) maps directly to
        array[int(x), int(y)] — x is the row axis, y is the column axis.
        """
        image = self._raw_image
        if image is None:
            return ""

        row, col = int(x), int(y)
        if row < 0 or col < 0 or row >= image.shape[0] or col >= image.shape[1]:
            return ""

        intensity = image[row, col]
        return f"x={x:.1f}  y={y:.1f}  I={intensity:.0f}"

    # =====================================================================
    # Device dimension helpers (called from background thread)
    # =====================================================================

    def _get_image_dimensions(self, image_plugin: Any = None) -> tuple[int | None, int | None]:
        """Get image width and height from plugin or cam."""
        try:
            if image_plugin is not None:
                w = getattr(image_plugin, "width", None)
                h = getattr(image_plugin, "height", None)
                if w is not None and h is not None:
                    width, height = int(w.get()), int(h.get())
                    if width > 0 and height > 0:
                        return width, height

            cam = getattr(self._device, "cam", None)
            if cam is not None:
                size = getattr(cam, "array_size", None)
                if size is not None:
                    dims = size.get()
                    if hasattr(dims, "array_size_x") and hasattr(dims, "array_size_y"):
                        width = int(dims.array_size_x)
                        height = int(dims.array_size_y)
                        if width > 0 and height > 0:
                            return width, height
        except Exception as e:
            logger.debug(f"Failed to get image dimensions: {e}")

        return None, None

    # =====================================================================
    # Lifecycle
    # =====================================================================

    def close(self) -> None:
        """Stop background thread and timers."""
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
        if hasattr(self, "_ui_timer") and self._ui_timer is not None:
            self._ui_timer.stop()
        super().close()
