"""Volume visualization for 3D data.

Provides a multi-slice viewer for volumetric data with
orthogonal slice views (XY, XZ, YZ).
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
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.visualization_plugin import VisualizationPlugin
from lucid.visualization.base import BaseVisualizationWidget
from lucid.visualization.spec import (
    DataCharacteristics,
    FieldType,
    VisualizationSpec,
    VizType,
)
from lucid.visualization.theme import ThemedVisualizationMixin, VisualizationColors

if TYPE_CHECKING:
    import pyqtgraph as pg

    from lucid.acquire.buffer import MultiStreamBuffer


class VolumeVisualization(ThemedVisualizationMixin, BaseVisualizationWidget):
    """Volume visualization for 3D data.

    Displays 3D volumetric data with orthogonal slice views
    (XY, XZ, YZ planes). Features:
    - Three synchronized slice views
    - Slice position sliders
    - Crosshair linking between views
    - Colormap selection
    """

    def __init__(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the volume visualization."""
        self._volume_data: np.ndarray | None = None
        self._xy_view: pg.ImageView | None = None
        self._xz_view: pg.ImageView | None = None
        self._yz_view: pg.ImageView | None = None

        # Current slice positions
        self._x_slice = 0
        self._y_slice = 0
        self._z_slice = 0

        super().__init__(spec, buffer, parent)
        self._setup_theme()

    def _setup_ui(self) -> None:
        """Setup the volume viewer UI."""
        import pyqtgraph as pg

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)

        # Create splitter for views
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # XY view (top-down)
        xy_widget = QWidget()
        xy_layout = QVBoxLayout(xy_widget)
        xy_layout.setContentsMargins(2, 2, 2, 2)
        xy_layout.addWidget(QLabel("XY (Z slice)"))

        self._xy_view = pg.ImageView()
        self._xy_view.ui.roiBtn.hide()
        self._xy_view.ui.menuBtn.hide()
        xy_layout.addWidget(self._xy_view)

        self._z_slider = QSlider(Qt.Orientation.Horizontal)
        self._z_slider.valueChanged.connect(self._on_z_changed)
        xy_layout.addWidget(self._z_slider)

        splitter.addWidget(xy_widget)

        # XZ view (front)
        xz_widget = QWidget()
        xz_layout = QVBoxLayout(xz_widget)
        xz_layout.setContentsMargins(2, 2, 2, 2)
        xz_layout.addWidget(QLabel("XZ (Y slice)"))

        self._xz_view = pg.ImageView()
        self._xz_view.ui.roiBtn.hide()
        self._xz_view.ui.menuBtn.hide()
        xz_layout.addWidget(self._xz_view)

        self._y_slider = QSlider(Qt.Orientation.Horizontal)
        self._y_slider.valueChanged.connect(self._on_y_changed)
        xz_layout.addWidget(self._y_slider)

        splitter.addWidget(xz_widget)

        # YZ view (side)
        yz_widget = QWidget()
        yz_layout = QVBoxLayout(yz_widget)
        yz_layout.setContentsMargins(2, 2, 2, 2)
        yz_layout.addWidget(QLabel("YZ (X slice)"))

        self._yz_view = pg.ImageView()
        self._yz_view.ui.roiBtn.hide()
        self._yz_view.ui.menuBtn.hide()
        yz_layout.addWidget(self._yz_view)

        self._x_slider = QSlider(Qt.Orientation.Horizontal)
        self._x_slider.valueChanged.connect(self._on_x_changed)
        yz_layout.addWidget(self._x_slider)

        splitter.addWidget(yz_widget)

        main_layout.addWidget(splitter)

        # Status bar
        self._status_label = QLabel("")
        main_layout.addWidget(self._status_label)

        # Add to parent layout
        container = QWidget()
        container.setLayout(main_layout)
        self._layout.addWidget(container)

    def _create_toolbar(self) -> QHBoxLayout:
        """Create the toolbar."""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Volume field selector
        vol_label = QLabel("Data:")
        self._vol_combo = QComboBox()
        self._vol_combo.setMinimumWidth(120)
        self._vol_combo.currentTextChanged.connect(self._on_volume_field_changed)
        toolbar.addWidget(vol_label)
        toolbar.addWidget(self._vol_combo)

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

    def _on_volume_field_changed(self, field: str) -> None:
        """Handle volume field change."""
        if field:
            self._spec.image_field = field

    def _on_colormap_changed(self, cmap_name: str) -> None:
        """Handle colormap change."""
        self._spec.colormap = cmap_name
        self._apply_colormap(cmap_name)

    def _apply_colormap(self, cmap_name: str) -> None:
        """Apply colormap to all views."""
        try:
            import pyqtgraph as pg

            cmap = pg.colormap.get(cmap_name)
            if cmap:
                for view in [self._xy_view, self._xz_view, self._yz_view]:
                    if view:
                        view.setColorMap(cmap)
        except Exception as e:
            logger.debug("Could not apply colormap: {}", e)

    def _on_auto_levels(self) -> None:
        """Auto-adjust levels in all views."""
        for view in [self._xy_view, self._xz_view, self._yz_view]:
            if view:
                view.autoLevels()

    def _on_x_changed(self, value: int) -> None:
        """Handle X slice change."""
        self._x_slice = value
        self._update_yz_slice()
        self._update_status()

    def _on_y_changed(self, value: int) -> None:
        """Handle Y slice change."""
        self._y_slice = value
        self._update_xz_slice()
        self._update_status()

    def _on_z_changed(self, value: int) -> None:
        """Handle Z slice change."""
        self._z_slice = value
        self._update_xy_slice()
        self._update_status()

    def _update_xy_slice(self) -> None:
        """Update XY slice view."""
        if self._volume_data is not None and self._xy_view:
            if self._z_slice < self._volume_data.shape[2]:
                self._xy_view.setImage(
                    self._volume_data[:, :, self._z_slice],
                    autoLevels=False,
                )

    def _update_xz_slice(self) -> None:
        """Update XZ slice view."""
        if self._volume_data is not None and self._xz_view:
            if self._y_slice < self._volume_data.shape[1]:
                self._xz_view.setImage(
                    self._volume_data[:, self._y_slice, :],
                    autoLevels=False,
                )

    def _update_yz_slice(self) -> None:
        """Update YZ slice view."""
        if self._volume_data is not None and self._yz_view:
            if self._x_slice < self._volume_data.shape[0]:
                self._yz_view.setImage(
                    self._volume_data[self._x_slice, :, :],
                    autoLevels=False,
                )

    def _update_status(self) -> None:
        """Update status bar."""
        if self._volume_data is not None:
            shape = self._volume_data.shape
            self._status_label.setText(
                f"Volume: {shape[0]}×{shape[1]}×{shape[2]} | "
                f"Slice: ({self._x_slice}, {self._y_slice}, {self._z_slice})"
            )

    def _on_new_point(self, seq_num: int, data: dict[str, Any]) -> None:
        """Handle new data point."""
        image_field = self._spec.image_field

        # Auto-detect on first point
        if seq_num == 1:
            self._update_field_selectors(data)

            if not image_field:
                # Find 2D or 3D array field
                for key, val in data.items():
                    if hasattr(val, "shape") and len(val.shape) >= 2:
                        image_field = key
                        self._spec.image_field = key
                        break

        if not image_field or image_field not in data:
            return

        value = data[image_field]

        # Handle 2D array per event (build 3D stack)
        if hasattr(value, "shape"):
            arr = np.array(value, dtype=np.float64)

            if len(arr.shape) == 2:
                # Stack 2D images into 3D volume
                if self._volume_data is None:
                    # Initialize with first image
                    self._volume_data = arr[..., np.newaxis]
                else:
                    # Stack along third dimension
                    self._volume_data = np.concatenate(
                        [self._volume_data, arr[..., np.newaxis]],
                        axis=2,
                    )

            elif len(arr.shape) == 3:
                # Direct 3D data
                self._volume_data = arr

            self._update_sliders()
            self._update_all_slices()

    def _update_field_selectors(self, data: dict[str, Any]) -> None:
        """Update field combo boxes."""
        array_fields = []
        for key, val in data.items():
            if hasattr(val, "shape") and len(val.shape) >= 2:
                array_fields.append(key)

        self._vol_combo.blockSignals(True)
        self._vol_combo.clear()
        self._vol_combo.addItems(array_fields)
        if self._spec.image_field in array_fields:
            self._vol_combo.setCurrentText(self._spec.image_field)
        self._vol_combo.blockSignals(False)

    def _update_sliders(self) -> None:
        """Update slider ranges based on volume shape."""
        if self._volume_data is None:
            return

        nx, ny, nz = self._volume_data.shape

        self._x_slider.blockSignals(True)
        self._y_slider.blockSignals(True)
        self._z_slider.blockSignals(True)

        self._x_slider.setMaximum(max(0, nx - 1))
        self._y_slider.setMaximum(max(0, ny - 1))
        self._z_slider.setMaximum(max(0, nz - 1))

        # Set to middle slices
        self._x_slice = nx // 2
        self._y_slice = ny // 2
        self._z_slice = nz // 2

        self._x_slider.setValue(self._x_slice)
        self._y_slider.setValue(self._y_slice)
        self._z_slider.setValue(self._z_slice)

        self._x_slider.blockSignals(False)
        self._y_slider.blockSignals(False)
        self._z_slider.blockSignals(False)

    def _update_all_slices(self) -> None:
        """Update all slice views."""
        self._update_xy_slice()
        self._update_xz_slice()
        self._update_yz_slice()
        self._update_status()

    def _on_clear(self) -> None:
        """Handle clear request."""
        self._volume_data = None
        for view in [self._xy_view, self._xz_view, self._yz_view]:
            if view:
                view.clear()
        self._status_label.setText("")

    def _apply_viz_colors(self, colors: VisualizationColors) -> None:
        """Apply theme colors."""
        for view in [self._xy_view, self._xz_view, self._yz_view]:
            if view:
                vb = view.getView()
                if vb:
                    vb.setBackgroundColor(colors.background)

    def _export_data(self, format: str) -> bytes:
        """Export volume data."""
        if self._volume_data is None:
            return b""

        if format == "json":
            data = {
                "image_field": self._spec.image_field,
                "shape": self._volume_data.shape,
            }
            return json.dumps(data, indent=2).encode("utf-8")

        else:
            raise ValueError(f"Unsupported export format: {format}")

    def get_supported_export_formats(self) -> list[str]:
        return ["json"]


class VolumeVisualizationPlugin(VisualizationPlugin):
    """Plugin for volume visualization."""

    @property
    def name(self) -> str:
        return "volume"

    @property
    def display_name(self) -> str:
        return "Volume Viewer"

    @property
    def icon(self) -> str:
        return "cube"

    @property
    def description(self) -> str:
        return "3D volume viewer with orthogonal slice views"

    def can_handle(self, characteristics: DataCharacteristics) -> int:
        """Check if data is suitable for volume visualization.

        Best for:
        - 3D array data
        - 2D dependent variable in 2D+ scan
        """
        # Check for 3D array dependent variable
        dep_type = characteristics.get_dep_field_type()

        if dep_type == FieldType.ARRAY_3D:
            return 80  # Excellent match

        # Check for 2D arrays in multi-dimensional scan
        if characteristics.ndim >= 2:
            for info in characteristics.field_info.values():
                if len(info.shape) >= 2:
                    return 70

        # Can handle 2D images stacked over 1D scan
        if dep_type == FieldType.ARRAY_2D and characteristics.ndim >= 1:
            return 60

        return 0

    def create_widget(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> VolumeVisualization:
        return VolumeVisualization(spec, buffer, parent)

    def get_default_spec(
        self, characteristics: DataCharacteristics
    ) -> VisualizationSpec:
        return VisualizationSpec(
            viz_type=VizType.VOLUME,
            characteristics=characteristics,
            image_field=characteristics.get_array_fields(ndim=3)[0]
            if characteristics.get_array_fields(ndim=3)
            else characteristics.get_array_fields(ndim=2)[0]
            if characteristics.get_array_fields(ndim=2)
            else None,
        )
