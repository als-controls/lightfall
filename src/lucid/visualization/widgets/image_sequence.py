"""Image sequence visualization for 2D array data.

Provides a viewer for sequences of images (camera data) with
pyqtgraph's built-in timeline navigation, ROI selection, and
1D statistics plotting.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.visualization_plugin import VisualizationPlugin
from lucid.visualization.base import BaseVisualizationWidget
from lucid.visualization.spec import (
    DataCharacteristics,
    FieldType,
    VisualizationSpec,
)
from lucid.visualization.theme import ThemedVisualizationMixin, VisualizationColors
from lucid.visualization.widgets.time_axis import HumanReadableTimeAxis

if TYPE_CHECKING:
    from lucid.acquire.buffer import MultiStreamBuffer


class ImageStackVisualization(ThemedVisualizationMixin, BaseVisualizationWidget):
    """Image stack visualization for camera/detector data.

    Displays a sequence of 2D images with:
    - Built-in timeline navigation (pyqtgraph's roiPlot)
    - Human-readable time axis labels
    - Optional ROI selection for statistics
    - 1D plot showing ROI statistics over time
    - Contrast/brightness controls via histogram
    """

    def __init__(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the image stack visualization."""
        self._image_view: pg.ImageView | None = None
        self._images: list[np.ndarray] = []
        self._time_values: list[float] = []
        self._start_time: float | None = None
        self._current_frame = 0

        # ROI-related state
        self._roi: pg.RectROI | None = None
        self._roi_curves: list[pg.PlotDataItem] = []

        super().__init__(spec, buffer, parent)
        self._setup_theme()

        # Load any existing data from buffer
        self._load_historical_data()

    def _setup_ui(self) -> None:
        """Setup the image viewer UI."""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)

        # Create image view with timeline enabled
        self._image_view = pg.ImageView()

        # Show the ROI plot (contains the timeline)
        self._image_view.ui.roiPlot.show()

        # Ensure the timeline has adequate height (not collapsed)
        self._image_view.ui.roiPlot.setMinimumHeight(80)

        # Set initial splitter sizes to give timeline ~20% of vertical space
        # The splitter contains [graphicsView, roiPlot]
        self._image_view.ui.splitter.setSizes([400, 100])

        # Replace bottom axis with human-readable time axis
        self._time_axis = HumanReadableTimeAxis(orientation="bottom")
        self._image_view.ui.roiPlot.setAxisItems({"bottom": self._time_axis})

        # Connect timeline changes
        self._image_view.sigTimeChanged.connect(self._on_time_changed)

        # Style the timeline bar: 3px normally, 5px on hover
        timeline = self._image_view.timeLine
        timeline.setPen(pg.mkPen("y", width=3))
        timeline.setHoverPen(pg.mkPen("y", width=5))

        # Hide built-in ROI and menu buttons (we have our own ROI control)
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()

        # Apply initial colormap
        self._apply_colormap(self._spec.colormap)

        main_layout.addWidget(self._image_view)

        # Add to parent layout
        container = QWidget()
        container.setLayout(main_layout)
        self._layout.addWidget(container)

    def _create_toolbar(self) -> QHBoxLayout:
        """Create the toolbar."""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Image field selector
        img_label = QLabel("Image:")
        self._img_combo = QComboBox()
        self._img_combo.setMinimumWidth(120)
        self._img_combo.currentTextChanged.connect(self._on_image_field_changed)
        toolbar.addWidget(img_label)
        toolbar.addWidget(self._img_combo)

        # Colormap selector
        cmap_label = QLabel("Colormap:")
        self._cmap_combo = QComboBox()
        self._cmap_combo.addItems([
            "viridis", "plasma", "inferno", "magma", "gray", "hot"
        ])
        self._cmap_combo.setCurrentText(self._spec.colormap)
        self._cmap_combo.currentTextChanged.connect(self._on_colormap_changed)
        toolbar.addWidget(cmap_label)
        toolbar.addWidget(self._cmap_combo)

        toolbar.addStretch()

        # ROI toggle button
        self._roi_btn = QPushButton("ROI")
        self._roi_btn.setCheckable(True)
        self._roi_btn.setToolTip("Enable region of interest for statistics")
        self._roi_btn.toggled.connect(self._on_roi_toggled)
        toolbar.addWidget(self._roi_btn)

        # ROI statistic selector (only visible when ROI enabled)
        self._roi_stat_combo = QComboBox()
        self._roi_stat_combo.addItems(["Mean", "Sum", "Max", "Min", "Std"])
        self._roi_stat_combo.setToolTip("Statistic to calculate over ROI")
        self._roi_stat_combo.currentTextChanged.connect(self._on_roi_stat_changed)
        self._roi_stat_combo.hide()
        toolbar.addWidget(self._roi_stat_combo)

        # Auto-levels button
        auto_btn = QPushButton("Auto Levels")
        auto_btn.clicked.connect(self._on_auto_levels)
        toolbar.addWidget(auto_btn)

        return toolbar

    def _on_image_field_changed(self, field: str) -> None:
        """Handle image field change."""
        if field:
            self._spec.image_field = field

    def _on_colormap_changed(self, cmap_name: str) -> None:
        """Handle colormap change."""
        self._spec.colormap = cmap_name
        self._apply_colormap(cmap_name)

    def _apply_colormap(self, cmap_name: str) -> None:
        """Apply colormap to image view."""
        try:
            cmap = pg.colormap.get(cmap_name)
            if cmap and self._image_view:
                self._image_view.setColorMap(cmap)
        except Exception as e:
            logger.debug("Could not apply colormap: {}", e)

    def _on_auto_levels(self) -> None:
        """Auto-adjust contrast levels."""
        if self._image_view:
            self._image_view.autoLevels()

    def _on_time_changed(self, ind: int, time: float) -> None:
        """Handle timeline position change.

        Args:
            ind: Frame index.
            time: Time value at the frame.
        """
        self._current_frame = ind
        self._update_status()

    def _on_roi_toggled(self, enabled: bool) -> None:
        """Toggle ROI visibility and 1D plot.

        Args:
            enabled: Whether ROI should be shown.
        """
        self._roi_stat_combo.setVisible(enabled)

        if enabled:
            self._create_roi()
            if self._roi:
                self._roi.show()
            self._update_roi_plot()
        else:
            if self._roi:
                self._roi.hide()
            self._clear_roi_curves()

    def _on_roi_stat_changed(self, stat: str) -> None:
        """Handle ROI statistic type change.

        Args:
            stat: Selected statistic name.
        """
        if self._roi_btn.isChecked():
            self._update_roi_plot()

    def _create_roi(self) -> None:
        """Create ROI if it doesn't exist."""
        if self._roi is not None or not self._images:
            return

        # Get image dimensions
        height, width = self._images[0].shape

        # Create ROI at center of image, 50% size
        roi_width = width // 2
        roi_height = height // 2
        roi_x = width // 4
        roi_y = height // 4

        self._roi = pg.RectROI(
            [roi_x, roi_y],
            [roi_width, roi_height],
            pen=pg.mkPen("r", width=2),
        )
        self._roi.addScaleHandle([1, 1], [0, 0])
        self._roi.addScaleHandle([0, 0], [1, 1])
        self._roi.addScaleHandle([1, 0], [0, 1])
        self._roi.addScaleHandle([0, 1], [1, 0])

        self._image_view.addItem(self._roi)
        self._roi.sigRegionChanged.connect(self._update_roi_plot)

    def _clear_roi_curves(self) -> None:
        """Remove ROI statistic curves from the timeline plot."""
        for curve in self._roi_curves:
            self._image_view.ui.roiPlot.removeItem(curve)
        self._roi_curves.clear()

    def _update_roi_plot(self) -> None:
        """Calculate and plot ROI statistics over all frames.

        Uses efficient numpy slicing instead of per-frame getArrayRegion calls.
        """
        if not self._roi or not self._images or not self._image_view:
            return

        # Get ROI bounds in pixel coordinates (much faster than getArrayRegion per frame)
        pos = self._roi.pos()
        size = self._roi.size()

        # Convert to integer pixel indices, clamped to image bounds
        img_h, img_w = self._images[0].shape
        x0 = max(0, int(pos.x()))
        y0 = max(0, int(pos.y()))
        x1 = min(img_w, int(pos.x() + size.x()))
        y1 = min(img_h, int(pos.y() + size.y()))

        # Check for valid ROI region
        if x1 <= x0 or y1 <= y0:
            self._clear_roi_curves()
            return

        # Stack all images and extract ROI region in one operation
        # Shape: (n_frames, height, width) -> (n_frames, roi_h, roi_w)
        stack = np.array(self._images)
        roi_data = stack[:, y0:y1, x0:x1]

        # Get selected statistic and compute over spatial axes (1, 2)
        stat_name = self._roi_stat_combo.currentText()
        if stat_name == "Mean":
            roi_values = np.mean(roi_data, axis=(1, 2))
        elif stat_name == "Sum":
            roi_values = np.sum(roi_data, axis=(1, 2))
        elif stat_name == "Max":
            roi_values = np.max(roi_data, axis=(1, 2))
        elif stat_name == "Min":
            roi_values = np.min(roi_data, axis=(1, 2))
        elif stat_name == "Std":
            roi_values = np.std(roi_data, axis=(1, 2))
        else:
            roi_values = np.mean(roi_data, axis=(1, 2))

        # Clear existing curves and plot new
        self._clear_roi_curves()

        if len(roi_values) > 0 and self._time_values:
            n_points = min(len(roi_values), len(self._time_values))
            curve = self._image_view.ui.roiPlot.plot(
                x=np.array(self._time_values[:n_points]),
                y=roi_values[:n_points],
                pen=pg.mkPen("c", width=2),
                name=f"ROI {stat_name}",
            )
            self._roi_curves.append(curve)

    def _update_status(self) -> None:
        """Update the time axis label with current frame info."""
        total = len(self._images)
        current = self._current_frame + 1 if total > 0 else 0

        # Show frame and time info in the axis label
        if self._time_values and 0 <= self._current_frame < len(self._time_values):
            time_val = self._time_values[self._current_frame]
            label = f"Frame {current}/{total} | Time: {time_val:.3f}s"
        else:
            label = f"Frame {current}/{total}"

        self._time_axis.setLabel(label)

    def _on_new_point(self, seq_num: int, data: dict[str, Any]) -> None:
        """Handle new data point."""
        image_field = self._spec.image_field

        # Auto-detect image field on first point
        if seq_num == 1:
            self._update_field_selectors(data)

            if not image_field:
                # Find first 2D array field
                for key, val in data.items():
                    if hasattr(val, "shape") and len(val.shape) == 2:
                        image_field = key
                        self._spec.image_field = key
                        break

        if not image_field or image_field not in data:
            return

        value = data[image_field]

        # Ensure it's a 2D array
        if not hasattr(value, "shape") or len(value.shape) != 2:
            return

        # Store image
        img = np.array(value, dtype=np.float64)
        self._images.append(img)

        # Get timestamp from buffer
        timestamp = self._get_event_timestamp()

        # Make time relative to first frame (start at 0)
        if self._start_time is None:
            self._start_time = timestamp
        relative_time = timestamp - self._start_time
        self._time_values.append(relative_time)

        # Update image stack
        self._update_image_stack()

    def _get_event_timestamp(self) -> float:
        """Get the timestamp of the latest event from buffer.

        Returns:
            Timestamp in seconds, or 0 if not available.
        """
        stream_buffer = self.stream_buffer
        if stream_buffer:
            timestamps = stream_buffer.get_timestamps()
            if timestamps:
                return timestamps[-1]
        return 0.0

    def _update_image_stack(self) -> None:
        """Update ImageView with current image stack and time values."""
        if not self._images or not self._image_view:
            return

        stack = np.array(self._images)

        # Use time values if we have them
        if self._time_values:
            self._image_view.setImage(
                stack,
                xvals=np.array(self._time_values),
                axes={"t": 0, "y": 1, "x": 2},
                autoLevels=len(self._images) == 1,  # Only auto on first frame
            )
        else:
            self._image_view.setImage(
                stack,
                axes={"t": 0, "y": 1, "x": 2},
                autoLevels=len(self._images) == 1,
            )

        # Jump to latest frame
        self._image_view.setCurrentIndex(len(self._images) - 1)
        self._current_frame = len(self._images) - 1
        self._update_status()

        # Update ROI plot if enabled
        if self._roi_btn.isChecked():
            self._update_roi_plot()

    def _update_field_selectors(self, data: dict[str, Any]) -> None:
        """Update field combo boxes."""
        # Find 2D array fields
        array_fields = []
        for key, val in data.items():
            if hasattr(val, "shape") and len(val.shape) == 2:
                array_fields.append(key)

        self._img_combo.blockSignals(True)
        self._img_combo.clear()
        self._img_combo.addItems(array_fields)
        if self._spec.image_field in array_fields:
            self._img_combo.setCurrentText(self._spec.image_field)
        self._img_combo.blockSignals(False)

    def _load_historical_data(self) -> None:
        """Load any existing data from the buffer.

        This is called on initialization to support switching visualizations
        after data has already been collected.
        """
        stream_buffer = self.stream_buffer
        if not stream_buffer:
            return

        image_field = self._spec.image_field

        # Try to find image field if not specified
        if not image_field:
            for field_name in stream_buffer.field_names:
                info = stream_buffer.get_field_info(field_name)
                shape = info.get("shape", [])
                if len(shape) == 2:
                    image_field = field_name
                    self._spec.image_field = image_field
                    break

        if not image_field:
            return

        # Load historical images
        historical_data = stream_buffer.get_data(image_field)
        if not historical_data:
            return

        # Load historical timestamps
        historical_timestamps = stream_buffer.get_timestamps()

        logger.debug(
            "Loading {} historical images from buffer field '{}'",
            len(historical_data),
            image_field,
        )

        for i, value in enumerate(historical_data):
            if hasattr(value, "shape") and len(value.shape) == 2:
                img = np.array(value, dtype=np.float64)
                self._images.append(img)

                # Get corresponding timestamp
                if i < len(historical_timestamps):
                    ts = historical_timestamps[i]
                    if self._start_time is None:
                        self._start_time = ts
                    self._time_values.append(ts - self._start_time)
                else:
                    # Fallback: use index as time
                    self._time_values.append(float(i))

                # Update field selectors on first image
                if i == 0:
                    self._update_field_selectors({image_field: value})

        if self._images:
            # Update image stack
            self._update_image_stack()

    def _on_clear(self) -> None:
        """Handle clear request."""
        self._images.clear()
        self._time_values.clear()
        self._start_time = None
        self._current_frame = 0

        if self._roi:
            self._roi.hide()
            self._roi = None

        self._clear_roi_curves()

        if self._image_view:
            self._image_view.clear()

        self._update_status()

    def _apply_viz_colors(self, colors: VisualizationColors) -> None:
        """Apply theme colors."""
        if self._image_view:
            # ImageView doesn't have setBackground - use the view's background
            view = self._image_view.getView()
            if view:
                view.setBackgroundColor(colors.background)

            # Also set the histogram widget background
            hist_widget = self._image_view.getHistogramWidget()
            if hist_widget:
                hist_widget.setBackground(colors.background)

            # Set timeline plot background
            if self._image_view.ui.roiPlot:
                self._image_view.ui.roiPlot.setBackground(colors.background)

    def _export_data(self, format: str) -> bytes:
        """Export image data."""
        if not self._images:
            return b""

        if format == "json":
            data = {
                "image_field": self._spec.image_field,
                "frame_count": len(self._images),
                "shape": self._images[0].shape if self._images else None,
                "time_values": self._time_values,
            }
            return json.dumps(data, indent=2).encode("utf-8")

        else:
            raise ValueError(f"Unsupported export format: {format}")

    def get_supported_export_formats(self) -> list[str]:
        return ["json"]


class ImageStackVisualizationPlugin(VisualizationPlugin):
    """Plugin for image stack visualization."""

    @property
    def name(self) -> str:
        return "image_stack"

    @property
    def display_name(self) -> str:
        return "Image Stack"

    @property
    def icon(self) -> str:
        return "images"

    @property
    def description(self) -> str:
        return "Image sequence viewer for camera/detector data"

    def can_handle(self, characteristics: DataCharacteristics) -> int:
        """Check if data is suitable for image stack.

        Best for:
        - 1D scans where dependent variable is 2D array (image)
        - Camera/detector data
        """
        # Check for 2D array dependent variable
        dep_type = characteristics.get_dep_field_type()

        if dep_type == FieldType.ARRAY_2D:
            return 75  # Good match

        # Check field_info for 2D arrays
        for info in characteristics.field_info.values():
            if len(info.shape) == 2:
                return 70

        return 0

    def create_widget(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> ImageStackVisualization:
        return ImageStackVisualization(spec, buffer, parent)

    def get_default_spec(
        self, characteristics: DataCharacteristics
    ) -> VisualizationSpec:
        return VisualizationSpec.for_image_stack(characteristics)
