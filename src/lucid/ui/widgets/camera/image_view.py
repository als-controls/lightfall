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
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

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

        self._setup_ui()
        self._start_updates()

    def _setup_ui(self) -> None:
        """Build the viewer layout: [image + axes | histogram]."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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

        # HistogramLUTItem for color scale control
        self._histogram = pg.HistogramLUTItem()
        self._histogram.setImageItem(self._image_item)

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

        First frame: autoLevels=True to set initial LUT range.
        Subsequent frames: autoLevels=False to preserve user adjustments.
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

        auto_levels = self._first_frame
        self._image_item.setImage(arr, autoLevels=auto_levels)
        if self._first_frame:
            self._first_frame = False
            self._plot_item.getViewBox().autoRange()

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

    def close(self) -> None:
        """Stop updates and clean up."""
        if self._timer is not None:
            self._timer.stop()
        super().close()
