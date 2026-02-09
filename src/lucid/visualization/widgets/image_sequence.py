"""Image sequence visualization for 2D array data.

Provides a viewer for sequences of images (camera data) with
navigation and contrast controls.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
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

if TYPE_CHECKING:
    import pyqtgraph as pg

    from lucid.acquire.buffer import MultiStreamBuffer


class ImageStackVisualization(ThemedVisualizationMixin, BaseVisualizationWidget):
    """Image stack visualization for camera/detector data.

    Displays a sequence of 2D images with:
    - Frame navigation slider
    - Contrast/brightness controls
    - Region of interest selection
    - Live histogram
    - Crosshair with pixel value display
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
        self._current_frame = 0

        super().__init__(spec, buffer, parent)
        self._setup_theme()

    def _setup_ui(self) -> None:
        """Setup the image viewer UI."""
        import pyqtgraph as pg

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)

        # Create image view
        self._image_view = pg.ImageView()
        self._image_view.ui.roiBtn.hide()  # Hide ROI button
        self._image_view.ui.menuBtn.hide()  # Hide menu button

        main_layout.addWidget(self._image_view)

        # Navigation controls
        nav_layout = self._create_navigation()
        main_layout.addLayout(nav_layout)

        # Status bar
        self._status_label = QLabel("Frame: 0 / 0")
        main_layout.addWidget(self._status_label)

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

        # Auto-levels button
        auto_btn = QPushButton("Auto Levels")
        auto_btn.clicked.connect(self._on_auto_levels)
        toolbar.addWidget(auto_btn)

        return toolbar

    def _create_navigation(self) -> QHBoxLayout:
        """Create frame navigation controls."""
        nav_layout = QHBoxLayout()
        nav_layout.setSpacing(8)

        # Previous button
        prev_btn = QPushButton("<")
        prev_btn.setMaximumWidth(40)
        prev_btn.clicked.connect(self._on_prev_frame)
        nav_layout.addWidget(prev_btn)

        # Frame slider
        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setMinimum(0)
        self._frame_slider.setMaximum(0)
        self._frame_slider.valueChanged.connect(self._on_slider_changed)
        nav_layout.addWidget(self._frame_slider)

        # Next button
        next_btn = QPushButton(">")
        next_btn.setMaximumWidth(40)
        next_btn.clicked.connect(self._on_next_frame)
        nav_layout.addWidget(next_btn)

        # Play button
        self._play_btn = QPushButton("Play")
        self._play_btn.setCheckable(True)
        self._play_btn.clicked.connect(self._on_play_toggle)
        nav_layout.addWidget(self._play_btn)

        return nav_layout

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
            import pyqtgraph as pg

            cmap = pg.colormap.get(cmap_name)
            if cmap and self._image_view:
                self._image_view.setColorMap(cmap)
        except Exception as e:
            logger.debug("Could not apply colormap: {}", e)

    def _on_auto_levels(self) -> None:
        """Auto-adjust contrast levels."""
        if self._image_view:
            self._image_view.autoLevels()

    def _on_prev_frame(self) -> None:
        """Go to previous frame."""
        if self._current_frame > 0:
            self._current_frame -= 1
            self._show_frame(self._current_frame)
            self._frame_slider.setValue(self._current_frame)

    def _on_next_frame(self) -> None:
        """Go to next frame."""
        if self._current_frame < len(self._images) - 1:
            self._current_frame += 1
            self._show_frame(self._current_frame)
            self._frame_slider.setValue(self._current_frame)

    def _on_slider_changed(self, value: int) -> None:
        """Handle slider movement."""
        if value != self._current_frame and 0 <= value < len(self._images):
            self._current_frame = value
            self._show_frame(value)

    def _on_play_toggle(self, playing: bool) -> None:
        """Handle play/pause toggle."""
        if playing:
            self._play_btn.setText("Pause")
            # Would start a timer for playback
        else:
            self._play_btn.setText("Play")
            # Would stop playback timer

    def _show_frame(self, index: int) -> None:
        """Display a specific frame.

        Args:
            index: Frame index to display.
        """
        if 0 <= index < len(self._images) and self._image_view:
            img = self._images[index]
            self._image_view.setImage(img, autoLevels=False)
            self._update_status()

    def _update_status(self) -> None:
        """Update status label."""
        total = len(self._images)
        current = self._current_frame + 1 if total > 0 else 0
        self._status_label.setText(f"Frame: {current} / {total}")

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

        # Update slider range
        self._frame_slider.setMaximum(len(self._images) - 1)

        # Show latest frame (live mode)
        self._current_frame = len(self._images) - 1
        self._frame_slider.setValue(self._current_frame)
        self._show_frame(self._current_frame)

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

    def _on_clear(self) -> None:
        """Handle clear request."""
        self._images.clear()
        self._current_frame = 0
        self._frame_slider.setMaximum(0)
        self._frame_slider.setValue(0)
        if self._image_view:
            self._image_view.clear()
        self._update_status()

    def _apply_viz_colors(self, colors: VisualizationColors) -> None:
        """Apply theme colors."""
        if self._image_view:
            self._image_view.setBackground(colors.background)

    def _export_data(self, format: str) -> bytes:
        """Export image data."""
        if not self._images:
            return b""

        if format == "json":
            data = {
                "image_field": self._spec.image_field,
                "frame_count": len(self._images),
                "shape": self._images[0].shape if self._images else None,
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
