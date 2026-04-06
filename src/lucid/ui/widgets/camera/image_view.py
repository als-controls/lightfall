"""Scientific image viewer for ophyd area detector devices.

Displays live image data with:
- Axis ticks via PlotItem
- Histogram/LUT control
- Correct orientation (row 0 at top, CCW rotation applied via QTransform)
- Efficient frame updates via ImageItem.setImage()

The LUT is auto-scaled on the first frame received using percentile bounds
(second-lowest as min, 99th percentile as max). Subsequent frames preserve
the user's manual LUT adjustments until Reset LUT is pressed.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from lucid.ui.widgets.camera.dark_frames import DarkFrameManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class OphydImageView(QWidget):
    """PyQtGraph-based scientific image viewer for ophyd area detectors.

    Uses PlotItem for axes, ImageItem for rendering, and HistogramLUTItem
    for color scale control. Polls the device's image plugin at ~10 fps.

    Image orientation is handled entirely via QTransform on the ImageItem
    (CCW rotation + Y-axis inversion), not by preprocessing the data array.
    """

    def __init__(self, ophyd_device: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device = ophyd_device
        self._timer: QTimer | None = None
        self._first_frame = True
        self._log_mode = False
        self._raw_image: np.ndarray | None = None
        self._updating_levels = False  # guard against recursive level updates

        self._dark_manager = DarkFrameManager(
            device_name=ophyd_device.name if hasattr(ophyd_device, "name") else "unknown"
        )

        self._setup_ui()
        self._start_updates()

    def _setup_ui(self) -> None:
        """Build the viewer layout: [image + axes | histogram]."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)

        self._reset_lut_btn = QPushButton("Reset LUT")
        self._reset_lut_btn.setFixedHeight(24)
        self._reset_lut_btn.clicked.connect(self.reset_lut)
        toolbar.addWidget(self._reset_lut_btn)

        self._reset_axes_btn = QPushButton("Reset Axes")
        self._reset_axes_btn.setFixedHeight(24)
        self._reset_axes_btn.clicked.connect(self.reset_axes)
        toolbar.addWidget(self._reset_axes_btn)

        self._log_intensity_btn = QPushButton("Log Intensity")
        self._log_intensity_btn.setFixedHeight(24)
        self._log_intensity_btn.setCheckable(True)
        self._log_intensity_btn.toggled.connect(self._on_log_intensity_toggled)
        toolbar.addWidget(self._log_intensity_btn)

        self._bg_correct_btn = QPushButton("BG Correct")
        self._bg_correct_btn.setFixedHeight(24)
        self._bg_correct_btn.setCheckable(True)
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
        self._plot_item.getViewBox().invertY(True)
        self._plot_item.getViewBox().setAspectLocked(True)
        self._plot_item.setLabel("bottom", "x (px)")
        self._plot_item.setLabel("left", "y (px)")

        # ImageItem lives inside the PlotItem.
        # Orientation is handled via QTransform: CCW 90° rotation.
        # This is applied once and stays — no per-frame data manipulation.
        self._image_item = pg.ImageItem()
        self._image_item.setOpts(axisOrder="row-major")
        self._image_transform = QTransform()
        self._image_transform.rotate(-90)
        self._image_item.setTransform(self._image_transform)
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

        # HistogramLUTItem for color scale control (manually wired, not
        # using setImageItem, so we can decouple log-scaled display from
        # linear histogram bins/levels).
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

        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(16)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v / %m  (%p%)")
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

    def _start_updates(self) -> None:
        """Start polling the device for image data."""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_image)
        self._timer.start(100)  # ~10 fps

    def _update_image(self) -> None:
        """Poll device image plugin and update display."""
        if self._device is None:
            return

        try:
            image_plugin = None
            for attr in ("image1", "image"):
                plugin = getattr(self._device, attr, None)
                if plugin is not None and hasattr(plugin, "array_data"):
                    image_plugin = plugin
                    break

            if image_plugin is not None:
                image_data = image_plugin.array_data.get()
                if image_data is not None:
                    self._display_array(image_data, image_plugin)
                    self._update_roi_stats()
                    self._update_progress()
        except Exception as e:
            logger.warning(f"Failed to update image: {e}")

    def _display_array(self, array: np.ndarray, image_plugin: Any = None) -> None:
        """Process and display a numpy array.

        First frame: auto-scale histogram levels using percentile bounds.
        Subsequent frames: preserve user-adjusted levels.
        Log mode: ImageItem shows log1p(data); histogram stays in real units.
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

        # Background correction
        if self._bg_correct_btn.isChecked():
            arr = self._dark_manager.subtract(arr)

        # Always cache raw data
        self._raw_image = arr

        # Determine what to display
        if self._log_mode:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                display_arr = np.log1p(arr.astype(np.float64))
        else:
            display_arr = arr

        # Set image without auto-levels (we manage levels manually)
        self._image_item.setImage(display_arr, autoLevels=False)

        if self._first_frame:
            self._first_frame = False
            # Percentile-based LUT: second-lowest as min, 99th percentile as max
            self._auto_levels()
            self.reset_axes()

        # Apply current levels to the displayed image
        self._apply_display_levels()

        # Update histogram bins (throttled — only every Nth frame)
        self._frame_count = getattr(self, "_frame_count", 0) + 1
        if self._frame_count % 5 == 1:  # every 5th frame
            self._update_histogram(arr)

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

    def reset_lut(self) -> None:
        """Reset LUT using percentile-based bounds from current image."""
        if self._raw_image is not None:
            self._auto_levels()
            self._apply_display_levels()
        else:
            self._first_frame = True

    def reset_axes(self) -> None:
        """Reset view to fit the image bounds exactly.

        Sets the view range to match the image dimensions rather than
        using autoRange which can add padding or behave unexpectedly
        with transforms.
        """
        if self._raw_image is not None:
            h, w = self._raw_image.shape[:2]
            vb = self._plot_item.getViewBox()
            # Set range to image pixel bounds; the ImageItem's transform
            # maps these to view coordinates.
            bounds = self._image_item.mapRectToView(
                self._image_item.boundingRect()
            )
            vb.setRange(bounds, padding=0.02)
        else:
            self._plot_item.getViewBox().autoRange()

    def _auto_levels(self) -> None:
        """Set histogram levels using percentile-based bounds.

        Uses second-lowest value as min and 99th percentile as max,
        following Xi-CAM's approach. This excludes hot pixels and dead
        pixels from dominating the color scale.
        """
        arr = self._raw_image
        if arr is None:
            return

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)

            # Subsample large images for speed
            step = max(1, arr.size // 1_000_000)
            data = arr.ravel()[::step].astype(np.float64)

            img_min = np.nanmin(data)
            img_max = np.nanmax(data)

            if img_min == img_max:
                self._histogram.setLevels(float(img_min), float(img_min + 1))
                return

            # Second-lowest value as min (excludes dead pixels at exact min)
            lo = float(np.min(data, where=data > img_min, initial=img_max))
            # 99th percentile as max (excludes hot pixels)
            hi = float(np.nanpercentile(
                np.where(data < img_max, data, img_min), 99
            ))

            if lo >= hi:
                lo = float(img_min)
                hi = float(img_max)

            self._histogram.setLevels(lo, hi)

    def _on_log_intensity_toggled(self, checked: bool) -> None:
        """Toggle log intensity display and re-render the current frame."""
        self._log_mode = checked
        if self._raw_image is not None:
            if self._log_mode:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    display_arr = np.log1p(self._raw_image.astype(np.float64))
            else:
                display_arr = self._raw_image
            self._image_item.setImage(display_arr, autoLevels=False)
            self._apply_display_levels()

    def _update_histogram(self, arr: np.ndarray) -> None:
        """Compute histogram of raw data and update the histogram widget.

        Subsamples large images for speed. Called every Nth frame to avoid
        being a bottleneck.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            step = max(1, arr.size // 500_000)
            vals = arr.ravel()[::step]
            hist_y, hist_x = np.histogram(vals, bins=256)
            hist_x_centers = (hist_x[:-1] + hist_x[1:]) / 2
            self._histogram.plot.setData(hist_x_centers, hist_y)

    def _apply_display_levels(self) -> None:
        """Map histogram levels (real units) to the displayed ImageItem.

        In log mode the real-unit levels are transformed via log1p before
        being applied, so the ImageItem (which holds log-scaled pixels)
        gets the correct clipping range.
        """
        if self._updating_levels:
            return
        self._updating_levels = True
        try:
            lo, hi = self._histogram.getLevels()
            if self._log_mode:
                lo = np.log1p(max(lo, 0.0))
                hi = np.log1p(max(hi, 0.0))
            self._image_item.setLevels([lo, hi])
        finally:
            self._updating_levels = False

    def _on_gradient_changed(self) -> None:
        """Propagate colormap changes from the histogram to the ImageItem."""
        lut = self._histogram.gradient.getLookupTable(256)
        self._image_item.setLookupTable(lut)

    _STAT_FIELDS = ("min_value", "max_value", "mean_value", "total", "centroid_x", "centroid_y")

    def _update_roi_stats(self) -> None:
        stats_plugin = getattr(self._device, "roi_stat1", None)
        if stats_plugin is None:
            self._stats_text.setVisible(False)
            return
        try:
            lines = []
            for field in self._STAT_FIELDS:
                signal = getattr(stats_plugin, field, None)
                if signal is not None:
                    value = signal.get()
                    label = field.replace("_", " ").title()
                    if isinstance(value, float):
                        lines.append(f"{label}: {value:.1f}")
                    else:
                        lines.append(f"{label}: {value}")
            if lines:
                self._stats_text.setText("\n".join(lines))
                vb = self._plot_item.getViewBox()
                view_range = vb.viewRange()
                self._stats_text.setPos(view_range[0][1], view_range[1][0])
                self._stats_text.setVisible(True)
            else:
                self._stats_text.setVisible(False)
        except Exception as e:
            logger.debug(f"Failed to read ROI stats: {e}")
            self._stats_text.setVisible(False)

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

        Maps view coordinates back to pixel coordinates through the
        ImageItem's inverse transform to get the correct array index
        regardless of any rotation/flip applied.
        """
        image = self._raw_image
        if image is None:
            return ""

        from PySide6.QtCore import QPointF

        # Map view coords → ImageItem local coords (undoes the CCW rotation)
        view_pt = QPointF(x, y)
        px_pt = self._image_item.mapFromView(view_pt)
        col, row = int(px_pt.x()), int(px_pt.y())

        if row < 0 or col < 0 or row >= image.shape[0] or col >= image.shape[1]:
            return ""

        intensity = image[row, col]
        return f"x={col}  y={row}  I={intensity:.0f}"

    def _update_progress(self) -> None:
        """Update the acquisition progress bar from device counters."""
        cam = getattr(self._device, "cam", None)
        if cam is None:
            self._progress_bar.setVisible(False)
            return
        try:
            acquiring = getattr(cam, "acquire", None)
            if acquiring is None or not acquiring.get():
                self._progress_bar.setVisible(False)
                return
            # Try HDF5 plugin first
            hdf5 = getattr(self._device, "hdf5", None)
            if hdf5 is not None:
                capture = getattr(hdf5, "capture", None)
                if capture is not None and capture.get():
                    current = int(hdf5.num_captured.get())
                    total = int(cam.num_images.get())
                    self._progress_bar.setMaximum(total)
                    self._progress_bar.setValue(current)
                    self._progress_bar.setVisible(True)
                    return
            # Fall back to cam.array_counter
            counter = getattr(cam, "array_counter", None)
            num_images = getattr(cam, "num_images", None)
            if counter is not None and num_images is not None:
                current = int(counter.get())
                total = int(num_images.get())
                self._progress_bar.setMaximum(total)
                self._progress_bar.setValue(current)
                self._progress_bar.setVisible(True)
                return
            self._progress_bar.setVisible(False)
        except Exception:
            self._progress_bar.setVisible(False)

    def close(self) -> None:
        """Stop updates and clean up."""
        if self._timer is not None:
            self._timer.stop()
        super().close()
