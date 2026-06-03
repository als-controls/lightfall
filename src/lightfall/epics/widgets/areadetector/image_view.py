"""
PVImageView - Live image display widget for EPICS AreaDetector.

Provides a pyqtgraph-based image viewer with:
- EPICS subscription-based updates (no polling)
- Configurable rate limiting to protect the GUI
- Histogram for intensity scaling
- Colormap selection
- Crosshair with coordinate display
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Property, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lightfall.epics.widgets.style import (
    get_disconnected_color,
    get_success_color,
)

# Available colormaps
COLORMAPS = {
    "viridis": pg.colormap.get("viridis"),
    "plasma": pg.colormap.get("plasma"),
    "inferno": pg.colormap.get("inferno"),
    "magma": pg.colormap.get("magma"),
    "gray": pg.colormap.get("CET-L1"),
    "hot": pg.colormap.get("CET-L3"),
}


class PVImageView(QWidget):
    """
    Live image display widget for EPICS AreaDetector StdArrays plugin.

    Subscribes to ArrayData PV and displays images with rate limiting,
    colormap selection, histogram for intensity control, and crosshair
    with coordinate display.

    Attributes:
        prefix: The base detector prefix (e.g., "13SIM1:").
        image_suffix: The StdArrays plugin suffix (default "image1:").
        max_fps: Maximum display frame rate (default 30).

    Signals:
        connection_changed: Emitted when connection state changes.
        frame_received: Emitted when a new frame is displayed.
        cursor_moved: Emitted with (x, y, intensity) when cursor moves.

    Example:
        >>> viewer = PVImageView("13SIM1:", image_suffix="image1:")
        >>> viewer.show()
    """

    widget_type: ClassVar[str] = "PVImageView"
    widget_description: ClassVar[str] = "Live image display for AreaDetector"

    connection_changed = Signal(bool)
    frame_received = Signal(int)
    cursor_moved = Signal(float, float, float)

    def __init__(
        self,
        prefix: str = "",
        image_suffix: str = "image1:",
        max_fps: float = 30.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._prefix = prefix
        self._image_suffix = image_suffix
        self._image_prefix = f"{prefix}{image_suffix}" if prefix else ""

        self._max_fps = max_fps
        self._min_frame_interval = 1.0 / max_fps if max_fps > 0 else 0
        self._last_display_time = 0.0
        self._pending_frame: np.ndarray | None = None

        self._width = 0
        self._height = 0
        self._current_data: np.ndarray | None = None

        self._pvs: dict[str, Any] = {}
        self._values: dict[str, Any] = {}
        self._connected_pvs: set[str] = set()

        self._current_colormap = "viridis"
        self._auto_scale = True

        self._setup_ui()

        self._display_timer = QTimer(self)
        self._display_timer.setSingleShot(True)
        self._display_timer.timeout.connect(self._display_pending_frame)

        if prefix:
            QTimer.singleShot(0, self._connect_pvs)

    @Property(str)
    def prefix(self) -> str:
        return self._prefix

    @prefix.setter
    def prefix(self, value: str) -> None:
        if value != self._prefix:
            self._disconnect_pvs()
            self._prefix = value
            self._image_prefix = f"{value}{self._image_suffix}" if value else ""
            if value:
                self._connect_pvs()

    @Property(str)
    def image_suffix(self) -> str:
        return self._image_suffix

    @image_suffix.setter
    def image_suffix(self, value: str) -> None:
        if value != self._image_suffix:
            self._disconnect_pvs()
            self._image_suffix = value
            self._image_prefix = f"{self._prefix}{value}" if self._prefix else ""
            if self._prefix:
                self._connect_pvs()

    @Property(float)
    def max_fps(self) -> float:
        return self._max_fps

    @max_fps.setter
    def max_fps(self, value: float) -> None:
        self._max_fps = value
        self._min_frame_interval = 1.0 / value if value > 0 else 0

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Status Bar
        status_bar = QHBoxLayout()
        status_bar.setSpacing(8)

        self._conn_indicator = QLabel()
        self._conn_indicator.setFixedSize(12, 12)
        self._update_connection_indicator(False)
        status_bar.addWidget(self._conn_indicator)

        self._prefix_label = QLabel(self._image_prefix or "No prefix")
        status_bar.addWidget(self._prefix_label)

        status_bar.addStretch()

        self._frame_label = QLabel("Frame: ---")
        status_bar.addWidget(self._frame_label)

        main_layout.addLayout(status_bar)

        # Image Display with Histogram
        image_layout = QHBoxLayout()
        image_layout.setSpacing(4)

        self._graphics_widget = pg.GraphicsLayoutWidget()
        self._graphics_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self._plot = self._graphics_widget.addPlot()
        self._plot.setAspectLocked(True)
        self._plot.hideAxis("left")
        self._plot.hideAxis("bottom")

        self._image_item = pg.ImageItem()
        self._plot.addItem(self._image_item)

        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('y', width=1))
        self._hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('y', width=1))
        self._plot.addItem(self._vline, ignoreBounds=True)
        self._plot.addItem(self._hline, ignoreBounds=True)
        self._vline.setVisible(False)
        self._hline.setVisible(False)

        self._image_item.hoverEvent = self._on_image_hover
        self._image_item.setAcceptHoverEvents(True)

        image_layout.addWidget(self._graphics_widget, stretch=4)

        self._histogram = pg.HistogramLUTWidget()
        self._histogram.setImageItem(self._image_item)
        self._histogram.setFixedWidth(100)

        image_layout.addWidget(self._histogram, stretch=0)

        self._apply_colormap()

        main_layout.addLayout(image_layout)

        # Coordinate Display
        coord_layout = QHBoxLayout()
        coord_layout.setSpacing(16)

        self._coord_label = QLabel("x: ---  y: ---  I: ---")
        self._coord_label.setStyleSheet("font-family: monospace;")
        coord_layout.addWidget(self._coord_label)

        coord_layout.addStretch()

        coord_layout.addWidget(QLabel("Colormap:"))
        self._colormap_combo = QComboBox()
        self._colormap_combo.addItems(list(COLORMAPS.keys()))
        self._colormap_combo.setCurrentText(self._current_colormap)
        self._colormap_combo.currentTextChanged.connect(self._on_colormap_changed)
        coord_layout.addWidget(self._colormap_combo)

        self._auto_scale_check = QCheckBox("Auto")
        self._auto_scale_check.setChecked(self._auto_scale)
        self._auto_scale_check.setToolTip("Auto-scale intensity range")
        self._auto_scale_check.toggled.connect(self._on_auto_scale_toggled)
        coord_layout.addWidget(self._auto_scale_check)

        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setToolTip("Reset view to fit image")
        self._reset_btn.setFixedWidth(60)
        self._reset_btn.clicked.connect(self._on_reset_view)
        coord_layout.addWidget(self._reset_btn)

        main_layout.addLayout(coord_layout)

    def _connect_pvs(self) -> None:
        if not self._image_prefix:
            return

        from lightfall.epics.ca.pv import PV

        pv_fields = {
            "ArrayData": "array_data",
            "ArraySize0_RBV": "height",
            "ArraySize1_RBV": "width",
            "UniqueId_RBV": "unique_id",
            "EnableCallbacks": "enable_callbacks",
        }

        for field, name in pv_fields.items():
            pv_name = f"{self._image_prefix}{field}"
            pv = PV(pv_name, parent=self)
            pv.value_changed.connect(lambda v, n=name: self._on_pv_value(n, v))
            pv.connection_changed.connect(lambda c, n=name: self._on_pv_connection(n, c))
            pv.connect_pv()
            self._pvs[name] = pv

    def _disconnect_pvs(self) -> None:
        for pv in self._pvs.values():
            pv.disconnect_pv()
            pv.deleteLater()
        self._pvs.clear()
        self._connected_pvs.clear()
        self._values.clear()
        self._update_connection_indicator(False)

    @Slot(str, object)
    def _on_pv_value(self, name: str, value: Any) -> None:
        self._values[name] = value

        if name == "array_data":
            self._on_array_received(value)
        elif name == "width":
            self._width = int(value) if value is not None else 0
        elif name == "height":
            self._height = int(value) if value is not None else 0
        elif name == "unique_id":
            uid = int(value) if value is not None else 0
            self._frame_label.setText(f"Frame: {uid}")

    @Slot(str, bool)
    def _on_pv_connection(self, name: str, connected: bool) -> None:
        if connected:
            self._connected_pvs.add(name)
        else:
            self._connected_pvs.discard(name)

        essential = {"array_data", "width", "height"}
        is_connected = essential.issubset(self._connected_pvs)
        self._update_connection_indicator(is_connected)
        self.connection_changed.emit(is_connected)

    def _update_connection_indicator(self, connected: bool) -> None:
        color = get_success_color() if connected else get_disconnected_color()
        self._conn_indicator.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                border-radius: 6px;
            }}
        """)

    def _on_array_received(self, data: Any) -> None:
        if data is None:
            return

        if not isinstance(data, np.ndarray):
            data = np.array(data)

        now = time.monotonic()
        elapsed = now - self._last_display_time

        if elapsed >= self._min_frame_interval:
            self._display_frame(data)
            self._last_display_time = now
        else:
            self._pending_frame = data
            if not self._display_timer.isActive():
                delay_ms = int((self._min_frame_interval - elapsed) * 1000)
                self._display_timer.start(max(1, delay_ms))

    @Slot()
    def _display_pending_frame(self) -> None:
        if self._pending_frame is not None:
            self._display_frame(self._pending_frame)
            self._pending_frame = None
            self._last_display_time = time.monotonic()

    def _display_frame(self, data: np.ndarray) -> None:
        if data.ndim == 1 and self._width > 0 and self._height > 0:
            expected_size = self._width * self._height
            if data.size >= expected_size:
                data = data[:expected_size].reshape(self._height, self._width)
            else:
                return

        if data.ndim != 2:
            return

        self._current_data = data
        self._image_item.setImage(data, autoLevels=self._auto_scale)

        uid = self._values.get("unique_id", 0)
        self.frame_received.emit(int(uid) if uid else 0)

    def _apply_colormap(self) -> None:
        cmap = COLORMAPS.get(self._current_colormap)
        if cmap is not None:
            self._image_item.setColorMap(cmap)
            self._histogram.gradient.setColorMap(cmap)

    def _on_colormap_changed(self, name: str) -> None:
        self._current_colormap = name
        self._apply_colormap()

    def _on_auto_scale_toggled(self, checked: bool) -> None:
        self._auto_scale = checked
        if checked and self._current_data is not None:
            self._image_item.setImage(self._current_data, autoLevels=True)

    def _on_reset_view(self) -> None:
        self._plot.autoRange()

    def _on_image_hover(self, event) -> None:
        if event.isExit():
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            self._coord_label.setText("x: ---  y: ---  I: ---")
            return

        pos = event.pos()
        x, y = int(pos.x()), int(pos.y())

        self._vline.setPos(pos.x())
        self._hline.setPos(pos.y())
        self._vline.setVisible(True)
        self._hline.setVisible(True)

        intensity = "---"
        if self._current_data is not None:
            if 0 <= y < self._current_data.shape[0] and 0 <= x < self._current_data.shape[1]:
                intensity = f"{self._current_data[y, x]:.1f}"
                self.cursor_moved.emit(float(x), float(y), float(self._current_data[y, x]))

        self._coord_label.setText(f"x: {x}  y: {y}  I: {intensity}")

    def set_colormap(self, name: str) -> None:
        if name in COLORMAPS:
            self._colormap_combo.setCurrentText(name)

    def auto_scale_intensity(self) -> None:
        if self._current_data is not None:
            self._image_item.setImage(self._current_data, autoLevels=True)

    def set_levels(self, min_val: float, max_val: float) -> None:
        self._auto_scale_check.setChecked(False)
        self._image_item.setLevels([min_val, max_val])

    @property
    def is_connected(self) -> bool:
        essential = {"array_data", "width", "height"}
        return essential.issubset(self._connected_pvs)

    @property
    def frame_count(self) -> int:
        uid = self._values.get("unique_id", 0)
        return int(uid) if uid else 0

    @property
    def image_size(self) -> tuple[int, int]:
        return (self._width, self._height)

    @property
    def current_image(self) -> np.ndarray | None:
        return self._current_data

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "widget_type": self.widget_type,
            "widget_description": self.widget_description,
            "prefix": self._prefix,
            "image_prefix": self._image_prefix,
            "connected": self.is_connected,
            "connected_pvs": list(self._connected_pvs),
            "image_size": {"width": self._width, "height": self._height},
            "frame_count": self.frame_count,
            "has_image": self._current_data is not None,
            "colormap": self._current_colormap,
            "auto_scale": self._auto_scale,
            "max_fps": self._max_fps,
            "available_actions": [
                {"name": "set_colormap", "args": ["name"], "description": "Set colormap"},
                {"name": "auto_scale_intensity", "description": "Auto-scale intensity"},
                {"name": "set_levels", "args": ["min", "max"], "description": "Set intensity levels"},
            ],
        }

    def closeEvent(self, event) -> None:
        self._display_timer.stop()
        self._disconnect_pvs()
        super().closeEvent(event)
