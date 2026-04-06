"""Scientific image viewer for ophyd area detector devices.

Displays live image data with:
- Axis ticks via PlotItem
- Histogram/LUT control
- Correct orientation (row 0 at top)
- Efficient frame updates via ImageItem.setImage()

The LUT is auto-scaled on the first frame received, then held stable.
Users reset it manually via the Reset LUT button (added in a later task).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class OphydImageView(QWidget):
    """PyQtGraph-based scientific image viewer for ophyd area detectors.

    Uses PlotItem for axes, ImageItem for rendering, and HistogramLUTItem
    for color scale control. Polls the device's image plugin at ~10 fps.
    """

    def __init__(self, ophyd_device: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device = ophyd_device
        self._timer: QTimer | None = None
        self._first_frame = True
        self._log_mode = False
        self._raw_image: np.ndarray | None = None
        self._updating_levels = False  # guard against recursive level updates

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

        # ImageItem lives inside the PlotItem
        self._image_item = pg.ImageItem()
        self._image_item.setOpts(axisOrder="row-major")
        self._plot_item.addItem(self._image_item)

        # GraphicsView to host the PlotItem
        self._graphics_view = pg.GraphicsView()
        self._graphics_view.setCentralItem(self._plot_item)
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
        except Exception as e:
            logger.warning(f"Failed to update image: {e}")

    def _display_array(self, array: np.ndarray, image_plugin: Any = None) -> None:
        """Process and display a numpy array.

        First frame: auto-scale histogram levels from raw data range.
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

        # Always cache raw data
        self._raw_image = arr

        # Update histogram bins from raw data
        self._update_histogram(arr)

        # Determine what to display
        if self._log_mode:
            display_arr = np.log1p(arr.astype(np.float64))
        else:
            display_arr = arr

        # Set image without auto-levels (we manage levels manually)
        self._image_item.setImage(display_arr, autoLevels=False)

        if self._first_frame:
            self._first_frame = False
            # Set histogram levels from raw data range
            mn, mx = float(arr.min()), float(arr.max())
            if mn == mx:
                mx = mn + 1.0
            self._histogram.setLevels(mn, mx)
            self._plot_item.getViewBox().autoRange()

        # Apply current levels to the displayed image
        self._apply_display_levels()

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
        """Reset LUT to auto-scale on the next frame."""
        self._first_frame = True

    def reset_axes(self) -> None:
        """Reset view to fit the entire image."""
        self._plot_item.getViewBox().autoRange()

    def _on_log_intensity_toggled(self, checked: bool) -> None:
        """Toggle log intensity display and re-render the current frame."""
        self._log_mode = checked
        if self._raw_image is not None:
            if self._log_mode:
                display_arr = np.log1p(self._raw_image.astype(np.float64))
            else:
                display_arr = self._raw_image
            self._image_item.setImage(display_arr, autoLevels=False)
            self._apply_display_levels()

    def _update_histogram(self, arr: np.ndarray) -> None:
        """Compute histogram of raw data and update the histogram widget."""
        vals = arr.ravel()
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

    def close(self) -> None:
        """Stop updates and clean up."""
        if self._timer is not None:
            self._timer.stop()
        super().close()
