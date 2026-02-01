"""Base camera control widget for EPICS AreaDetector devices.

Provides a camera control UI with:
- Live image display via PVImageView
- Consolidated acquisition controls
- Extension point for device-specific panels
- TV mode support for continuous streaming
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lucid.devices.model import DeviceCategory
from lucid.ui.models.device_tree import DeviceTreeItem, NodeType
from lucid.ui.widgets.base_control import BaseControlWidget, register_control_widget
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


# AreaDetector image modes
IMAGE_MODES = ["Single", "Multiple", "Continuous"]

# AreaDetector shutter modes (subset - actual modes depend on hardware)
SHUTTER_MODES = ["Auto", "None", "EPICS PV", "Detector"]

# Detector states (from ADCore)
DETECTOR_STATES = {
    0: "Idle",
    1: "Acquire",
    2: "Readout",
    3: "Correct",
    4: "Saving",
    5: "Aborting",
    6: "Error",
    7: "Waiting",
    8: "Initializing",
    9: "Disconnected",
    10: "Aborted",
}


def is_camera_or_detector(item: DeviceTreeItem) -> bool:
    """Check if a DeviceTreeItem represents a camera or detector device.

    Args:
        item: The tree item to check.

    Returns:
        True if the item is a camera or detector device.
    """
    if item.node_type != NodeType.DEVICE:
        return False

    # Check device category from device_info
    if item.device_info:
        if item.device_info.category in (DeviceCategory.CAMERA, DeviceCategory.DETECTOR):
            return True

    # Check ophyd object class name
    if item.ophyd_obj is not None:
        class_name = type(item.ophyd_obj).__name__.lower()
        if any(kw in class_name for kw in ("camera", "detector", "areadetector", "img")):
            return True

    return False


class StatusIndicator(QFrame):
    """Small circular status indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._state = "off"
        self._update_style()

    def set_state(self, state: str) -> None:
        """Set indicator state: 'off', 'on', 'warning', 'error', 'disconnected'."""
        self._state = state
        self._update_style()

    def _update_style(self) -> None:
        colors = {
            "off": "#666666",
            "on": "#4CAF50",
            "warning": "#FFC107",
            "error": "#F44336",
            "disconnected": "#9E9E9E",
        }
        color = colors.get(self._state, colors["off"])
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: 7px;
                border: 1px solid #333;
            }}
        """)


class TVModeMixin:
    """Mixin for cameras that support continuous 'TV mode' streaming.

    TV mode allows continuous image streaming for live viewing. When
    acquisition plans need to run, TV mode should be paused to avoid
    conflicts, then resumed when the plan completes.
    """

    _tv_mode_active: bool = False
    _tv_mode_paused: bool = False

    def start_tv_mode(self) -> None:
        """Start continuous TV mode streaming.

        Sets image_mode to Continuous and starts acquisition.
        """
        self._tv_mode_active = True
        self._tv_mode_paused = False
        self._set_image_mode(2)  # Continuous
        self._start_acquire()
        logger.debug("Started TV mode")

    def stop_tv_mode(self) -> None:
        """Stop TV mode streaming."""
        self._tv_mode_active = False
        self._tv_mode_paused = False
        self._stop_acquire()
        logger.debug("Stopped TV mode")

    def pause_tv_mode(self) -> None:
        """Temporarily pause TV mode for acquisition plans.

        Should be called before running bluesky plans.
        """
        if self._tv_mode_active:
            self._tv_mode_paused = True
            self._stop_acquire()
            logger.debug("Paused TV mode")

    def resume_tv_mode(self) -> None:
        """Resume TV mode after acquisition plans complete.

        Should be called after bluesky plans finish.
        """
        if self._tv_mode_paused:
            self._tv_mode_paused = False
            self._set_image_mode(2)  # Continuous
            self._start_acquire()
            logger.debug("Resumed TV mode")

    def is_tv_mode_active(self) -> bool:
        """Check if TV mode is active (may be paused)."""
        return self._tv_mode_active

    def is_tv_mode_running(self) -> bool:
        """Check if TV mode is actively streaming (not paused)."""
        return self._tv_mode_active and not self._tv_mode_paused

    def _set_image_mode(self, mode: int) -> None:
        """Set image mode (override in subclass)."""
        pass

    def _start_acquire(self) -> None:
        """Start acquisition (override in subclass)."""
        pass

    def _stop_acquire(self) -> None:
        """Stop acquisition (override in subclass)."""
        pass


@register_control_widget
class CameraControlWidget(BaseControlWidget, TVModeMixin):
    """Control widget for EPICS AreaDetector cameras.

    Provides:
    - Live image display via PVImageView
    - Acquisition controls (time, count, mode, shutter)
    - State display
    - Extension point for device-specific panels

    Matches cameras and detectors by category. Device-specific subclasses
    can add specialized panels (cooler controls, temperature display, etc.).

    Class Attributes:
        display_name: Widget name shown in selector.
        priority: Widget priority (50 = category match, subclasses use 100).
        supported_tags: Tags that identify matching devices.
        supported_classes: Device class substrings for matching.
    """

    display_name: ClassVar[str] = "Camera Control"
    priority: ClassVar[int] = 50  # Category-level fallback

    # Subclasses override these for device-specific matching
    supported_tags: ClassVar[set[str]] = set()
    supported_classes: ClassVar[set[str]] = set()

    # Signals
    acquisition_started = Signal()
    acquisition_stopped = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self._prefix: str = ""
        self._cam_suffix: str = "cam1:"
        self._image_suffix: str = "image1:"

        # PV connections
        self._pvs: dict[str, Any] = {}
        self._values: dict[str, Any] = {}
        self._connected_pvs: set[str] = set()
        self._was_acquiring: bool = False

        # Widgets (initialized in _setup_ui)
        self._image_view: QWidget | None = None

        # Update timer
        self._update_timer: QTimer | None = None

        super().__init__(parent)

    @classmethod
    def can_control(cls, items: list[DeviceTreeItem]) -> bool:
        """Check if this widget can control the given items."""
        if len(items) != 1:
            return False

        item = items[0]

        # Check explicit tags first (Strategy C)
        if cls.supported_tags:
            device_tags = cls._get_device_tags(item)
            if cls.supported_tags & device_tags:
                return True

        # Fall back to device_class match (Strategy A)
        if cls.supported_classes:
            device_class = cls._get_device_class(item)
            if any(c.lower() in device_class.lower() for c in cls.supported_classes):
                return True

        # Base class: match any CAMERA or DETECTOR category
        if cls is CameraControlWidget:
            return is_camera_or_detector(item)

        return False

    @classmethod
    def _get_device_tags(cls, item: DeviceTreeItem) -> set[str]:
        """Extract device tags from a tree item."""
        if item.device_info and item.device_info.tags:
            return set(tag.lower() for tag in item.device_info.tags)
        return set()

    @classmethod
    def _get_device_class(cls, item: DeviceTreeItem) -> str:
        """Extract device class from a tree item."""
        if item.device_info and item.device_info.device_class:
            return item.device_info.device_class
        if item.ophyd_obj is not None:
            return type(item.ophyd_obj).__name__
        return ""

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the camera device to control."""
        self._items = items

        if items and len(items) == 1:
            item = items[0]
            self._extract_prefix(item)
            self._update_image_view_prefix()
            self._connect_pvs()
            self._start_updates()
            self._name_label.setText(item.name)
        else:
            self._disconnect_pvs()
            self._stop_updates()
            self._clear_display()

    def _extract_prefix(self, item: DeviceTreeItem) -> None:
        """Extract the EPICS PV prefix from the device."""
        # Try to get prefix from device_info
        if item.device_info and item.device_info.prefix:
            self._prefix = item.device_info.prefix
            return

        # Try to get prefix from ophyd device
        if item.ophyd_obj is not None:
            if hasattr(item.ophyd_obj, "prefix"):
                self._prefix = item.ophyd_obj.prefix
                return

        # Fallback to name
        self._prefix = f"{item.name}:"

    def _setup_ui(self) -> None:
        """Setup the camera control UI."""
        # Device name header
        self._name_label = QLabel("No Camera Selected")
        self._name_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self._layout.addWidget(self._name_label)

        # Status bar with connection indicator
        status_layout = QHBoxLayout()
        self._status_indicator = StatusIndicator()
        self._status_indicator.set_state("disconnected")
        status_layout.addWidget(self._status_indicator)

        self._status_label = QLabel("Disconnected")
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()
        self._layout.addLayout(status_layout)

        # Image view (placeholder - will be created when prefix is set)
        self._image_container = QWidget()
        self._image_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._image_container.setMinimumHeight(300)
        self._image_layout = QVBoxLayout(self._image_container)
        self._image_layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._image_container, stretch=1)

        # Acquisition panel
        acq_group = QGroupBox("Acquisition")
        acq_layout = QGridLayout(acq_group)
        acq_layout.setSpacing(8)

        row = 0

        # State display
        acq_layout.addWidget(QLabel("State:"), row, 0)
        self._state_label = QLabel("---")
        self._state_label.setStyleSheet("font-weight: bold;")
        acq_layout.addWidget(self._state_label, row, 1)

        # Shutter mode
        acq_layout.addWidget(QLabel("Shutter:"), row, 2)
        self._shutter_combo = QComboBox()
        self._shutter_combo.addItems(SHUTTER_MODES)
        self._shutter_combo.currentIndexChanged.connect(self._on_shutter_mode_changed)
        acq_layout.addWidget(self._shutter_combo, row, 3)

        row += 1

        # Acquire time
        acq_layout.addWidget(QLabel("Acquire Time:"), row, 0)
        self._acquire_time_edit = QLineEdit()
        self._acquire_time_edit.setValidator(QDoubleValidator(0, 10000, 6))
        self._acquire_time_edit.setPlaceholderText("seconds")
        self._acquire_time_edit.editingFinished.connect(self._on_acquire_time_changed)
        acq_layout.addWidget(self._acquire_time_edit, row, 1)

        # Image mode
        acq_layout.addWidget(QLabel("Image Mode:"), row, 2)
        self._image_mode_combo = QComboBox()
        self._image_mode_combo.addItems(IMAGE_MODES)
        self._image_mode_combo.currentIndexChanged.connect(self._on_image_mode_changed)
        acq_layout.addWidget(self._image_mode_combo, row, 3)

        row += 1

        # Num images
        acq_layout.addWidget(QLabel("Num Images:"), row, 0)
        self._num_images_edit = QLineEdit()
        self._num_images_edit.setValidator(QIntValidator(1, 1000000))
        self._num_images_edit.setPlaceholderText("count")
        self._num_images_edit.editingFinished.connect(self._on_num_images_changed)
        acq_layout.addWidget(self._num_images_edit, row, 1)

        row += 1

        # Acquire/Abort buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self._acquire_btn = QPushButton("ACQUIRE")
        self._acquire_btn.setMinimumHeight(36)
        self._acquire_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                font-size: 11pt;
                padding: 6px 20px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #28a745; }
            QPushButton:pressed { background-color: #1e7e34; }
            QPushButton:disabled { background-color: #666666; }
        """)
        self._acquire_btn.clicked.connect(self._on_acquire_clicked)
        btn_layout.addWidget(self._acquire_btn)

        self._abort_btn = QPushButton("ABORT")
        self._abort_btn.setMinimumHeight(36)
        self._abort_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                font-weight: bold;
                font-size: 11pt;
                padding: 6px 20px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #ff0000; }
            QPushButton:pressed { background-color: #990000; }
            QPushButton:disabled { background-color: #666666; }
        """)
        self._abort_btn.clicked.connect(self._on_abort_clicked)
        btn_layout.addWidget(self._abort_btn)

        acq_layout.addLayout(btn_layout, row, 0, 1, 4)

        self._layout.addWidget(acq_group)

        # Device-specific panels container
        self._device_panels_container = QWidget()
        self._device_panels_layout = QVBoxLayout(self._device_panels_container)
        self._device_panels_layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._device_panels_container)

        # Add device-specific panels (subclasses override)
        for panel in self._create_device_panels():
            self._device_panels_layout.addWidget(panel)

        # Initial state - disabled until connected
        self._set_controls_enabled(False)

    def _create_device_panels(self) -> list[QGroupBox]:
        """Create device-specific panels.

        Override in subclasses to add device-specific controls
        (cooler panel, temperature panel, etc.).

        Returns:
            List of QGroupBox widgets to add below acquisition controls.
        """
        return []

    def _update_image_view_prefix(self) -> None:
        """Update or create the image view with current prefix."""
        if not self._prefix:
            return

        try:
            from epics_pyside.widgets.areadetector import PVImageView

            # Remove old image view if exists
            if self._image_view is not None:
                self._image_view.deleteLater()

            # Create new image view
            self._image_view = PVImageView(
                prefix=self._prefix,
                image_suffix=self._image_suffix,
                max_fps=30.0,
            )
            self._image_layout.addWidget(self._image_view)

        except ImportError:
            logger.warning("epics_pyside not available, image view disabled")
            # Create placeholder label
            placeholder = QLabel("Image view requires epics_pyside")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._image_layout.addWidget(placeholder)

    def _connect_pvs(self) -> None:
        """Connect to AreaDetector camera PVs."""
        if not self._prefix:
            return

        self._disconnect_pvs()

        try:
            from epics_pyside.ca.pv import PV

            cam_prefix = f"{self._prefix}{self._cam_suffix}"

            # PVs to connect
            pv_fields = {
                "AcquireTime": "acquire_time",
                "AcquireTime_RBV": "acquire_time_rbv",
                "NumImages": "num_images",
                "NumImages_RBV": "num_images_rbv",
                "ImageMode": "image_mode",
                "ImageMode_RBV": "image_mode_rbv",
                "Acquire": "acquire",
                "DetectorState_RBV": "detector_state",
                "ShutterMode": "shutter_mode",
                "ShutterMode_RBV": "shutter_mode_rbv",
            }

            for field, name in pv_fields.items():
                pv_name = f"{cam_prefix}{field}"
                pv = PV(pv_name, parent=self)
                pv.value_changed.connect(lambda v, n=name: self._on_pv_value(n, v))
                pv.connection_changed.connect(lambda c, n=name: self._on_pv_connection(n, c))
                pv.connect_pv()
                self._pvs[name] = pv

        except ImportError:
            logger.warning("epics_pyside not available, PV connections disabled")

    def _disconnect_pvs(self) -> None:
        """Disconnect all PVs."""
        for pv in self._pvs.values():
            try:
                pv.disconnect_pv()
                pv.deleteLater()
            except Exception:
                pass
        self._pvs.clear()
        self._connected_pvs.clear()
        self._values.clear()

    @Slot(str, object)
    def _on_pv_value(self, name: str, value: Any) -> None:
        """Handle PV value updates."""
        # Extract scalar from array if needed
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]

        self._values[name] = value

        # Update UI
        if name == "acquire_time_rbv":
            self._update_acquire_time_display()
        elif name == "num_images_rbv":
            self._update_num_images_display()
        elif name == "image_mode_rbv":
            self._update_image_mode_display()
        elif name == "shutter_mode_rbv":
            self._update_shutter_mode_display()
        elif name == "acquire":
            self._update_acquire_state()
        elif name == "detector_state":
            self._update_detector_state()

    @Slot(str, bool)
    def _on_pv_connection(self, name: str, connected: bool) -> None:
        """Handle PV connection state changes."""
        if connected:
            self._connected_pvs.add(name)
        else:
            self._connected_pvs.discard(name)

        # Consider connected if essential PVs are connected
        essential = {"acquire", "detector_state"}
        is_connected = essential.issubset(self._connected_pvs)

        if is_connected:
            self._status_indicator.set_state("on")
            self._status_label.setText("Connected")
        else:
            self._status_indicator.set_state("disconnected")
            self._status_label.setText("Disconnected")

        self._set_controls_enabled(is_connected)

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable control widgets."""
        self._acquire_time_edit.setEnabled(enabled)
        self._num_images_edit.setEnabled(enabled)
        self._image_mode_combo.setEnabled(enabled)
        self._shutter_combo.setEnabled(enabled)
        self._acquire_btn.setEnabled(enabled)
        self._abort_btn.setEnabled(enabled)

    def _start_updates(self) -> None:
        """Start periodic updates."""
        if self._update_timer is None:
            self._update_timer = QTimer(self)
            self._update_timer.timeout.connect(self._periodic_update)
        self._update_timer.start(500)

    def _stop_updates(self) -> None:
        """Stop periodic updates."""
        if self._update_timer is not None:
            self._update_timer.stop()

    def _periodic_update(self) -> None:
        """Periodic update callback."""
        # Subclasses can override for device-specific updates
        pass

    def _clear_display(self) -> None:
        """Clear the display when no camera is selected."""
        self._name_label.setText("No Camera Selected")
        self._state_label.setText("---")
        self._status_indicator.set_state("disconnected")
        self._status_label.setText("Disconnected")
        self._set_controls_enabled(False)

    # === Display Updates ===

    def _update_acquire_time_display(self) -> None:
        """Update acquire time display."""
        if not self._acquire_time_edit.hasFocus():
            value = self._values.get("acquire_time_rbv")
            if value is not None:
                self._acquire_time_edit.setText(f"{float(value):.6g}")

    def _update_num_images_display(self) -> None:
        """Update num images display."""
        if not self._num_images_edit.hasFocus():
            value = self._values.get("num_images_rbv")
            if value is not None:
                self._num_images_edit.setText(str(int(value)))

    def _update_image_mode_display(self) -> None:
        """Update image mode display."""
        value = self._values.get("image_mode_rbv")
        if value is not None:
            idx = int(value)
            if 0 <= idx < len(IMAGE_MODES):
                self._image_mode_combo.blockSignals(True)
                self._image_mode_combo.setCurrentIndex(idx)
                self._image_mode_combo.blockSignals(False)

    def _update_shutter_mode_display(self) -> None:
        """Update shutter mode display."""
        value = self._values.get("shutter_mode_rbv")
        if value is not None:
            idx = int(value)
            if 0 <= idx < len(SHUTTER_MODES):
                self._shutter_combo.blockSignals(True)
                self._shutter_combo.setCurrentIndex(idx)
                self._shutter_combo.blockSignals(False)

    def _update_acquire_state(self) -> None:
        """Update acquire state."""
        acquiring = bool(self._values.get("acquire", 0))

        if acquiring and not self._was_acquiring:
            self.acquisition_started.emit()
        elif not acquiring and self._was_acquiring:
            self.acquisition_stopped.emit()

        self._was_acquiring = acquiring

    def _update_detector_state(self) -> None:
        """Update detector state display."""
        state = self._values.get("detector_state", 9)
        if isinstance(state, (list, tuple)):
            state = state[0] if state else 9

        state_name = DETECTOR_STATES.get(int(state), f"Unknown ({state})")
        self._state_label.setText(state_name)

        # Update status indicator color based on state
        if state == 0:  # Idle
            self._status_indicator.set_state("on")
        elif state in (1, 2, 3, 4, 7, 8):  # Active states
            self._status_indicator.set_state("warning")
        elif state in (5, 6, 10):  # Error/abort states
            self._status_indicator.set_state("error")
        else:
            self._status_indicator.set_state("disconnected")

    # === User Actions ===

    def _on_acquire_time_changed(self) -> None:
        """Handle acquire time entry."""
        try:
            value = float(self._acquire_time_edit.text())
            if "acquire_time" in self._pvs:
                self._pvs["acquire_time"].put(value)
        except ValueError:
            pass

    def _on_num_images_changed(self) -> None:
        """Handle num images entry."""
        try:
            value = int(self._num_images_edit.text())
            if "num_images" in self._pvs:
                self._pvs["num_images"].put(value)
        except ValueError:
            pass

    def _on_image_mode_changed(self, index: int) -> None:
        """Handle image mode selection."""
        if "image_mode" in self._pvs:
            self._pvs["image_mode"].put(index)

    def _on_shutter_mode_changed(self, index: int) -> None:
        """Handle shutter mode selection."""
        if "shutter_mode" in self._pvs:
            self._pvs["shutter_mode"].put(index)

    def _on_acquire_clicked(self) -> None:
        """Start acquisition."""
        if "acquire" in self._pvs:
            self._pvs["acquire"].put(1)

    def _on_abort_clicked(self) -> None:
        """Stop acquisition."""
        if "acquire" in self._pvs:
            self._pvs["acquire"].put(0)

    # === TVModeMixin Implementation ===

    def _set_image_mode(self, mode: int) -> None:
        """Set image mode for TV mode."""
        if "image_mode" in self._pvs:
            self._pvs["image_mode"].put(mode)

    def _start_acquire(self) -> None:
        """Start acquisition for TV mode."""
        if "acquire" in self._pvs:
            self._pvs["acquire"].put(1)

    def _stop_acquire(self) -> None:
        """Stop acquisition for TV mode."""
        if "acquire" in self._pvs:
            self._pvs["acquire"].put(0)

    # === Public API ===

    def acquire(self) -> None:
        """Start acquisition."""
        self._on_acquire_clicked()

    def abort(self) -> None:
        """Stop acquisition."""
        self._on_abort_clicked()

    @property
    def is_connected(self) -> bool:
        """Whether essential PVs are connected."""
        essential = {"acquire", "detector_state"}
        return essential.issubset(self._connected_pvs)

    @property
    def is_acquiring(self) -> bool:
        """Whether detector is currently acquiring."""
        return bool(self._values.get("acquire", 0))

    @property
    def detector_state(self) -> str:
        """Current detector state name."""
        state = self._values.get("detector_state", 9)
        if isinstance(state, (list, tuple)):
            state = state[0] if state else 9
        return DETECTOR_STATES.get(int(state), f"Unknown ({state})")

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        base_data = super().get_introspection_data()
        base_data.update({
            "prefix": self._prefix,
            "connected": self.is_connected,
            "is_acquiring": self.is_acquiring,
            "detector_state": self.detector_state,
            "tv_mode_active": self.is_tv_mode_active(),
            "available_actions": [
                {"name": "acquire", "description": "Start acquisition"},
                {"name": "abort", "description": "Stop acquisition"},
                {"name": "start_tv_mode", "description": "Start continuous streaming"},
                {"name": "stop_tv_mode", "description": "Stop continuous streaming"},
            ],
        })
        return base_data

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        self._stop_updates()
        self._disconnect_pvs()
        if self._image_view is not None:
            self._image_view.close()
        super().closeEvent(event)
