"""
PVAreaDetectorControls - Acquisition control panel for EPICS AreaDetector.

Provides controls for:
- Acquire time and period
- Number of images
- Image mode (Single/Multiple/Continuous)
- Acquire/Abort buttons
- Detector state display
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Property, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QFrame,
)

from lucid.epics.widgets.lineedit import PVLineEdit
from lucid.epics.widgets.combobox import PVComboBox
from lucid.epics.widgets.style import (
    get_success_color,
    get_error_color,
    get_warning_color,
    get_disconnected_color,
)


# AreaDetector image modes
IMAGE_MODES = ["Single", "Multiple", "Continuous"]

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


class StatusIndicator(QFrame):
    """A small circular status indicator."""

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
            "on": get_success_color(),
            "warning": get_warning_color(),
            "error": get_error_color(),
            "disconnected": get_disconnected_color(),
        }
        color = colors.get(self._state, colors["off"])
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: 7px;
                border: 1px solid #333;
            }}
        """)


class PVAreaDetectorControls(QWidget):
    """
    Acquisition control panel for EPICS AreaDetector camera plugin.

    Provides controls for acquisition time, period, number of images,
    image mode, and acquire/abort buttons with detector state display.

    Attributes:
        prefix: The base detector prefix (e.g., "13SIM1:").
        cam_suffix: The camera plugin suffix (default "cam1:").

    Signals:
        connection_changed: Emitted when connection state changes.
        acquisition_started: Emitted when acquisition begins.
        acquisition_stopped: Emitted when acquisition ends.

    Example:
        >>> controls = PVAreaDetectorControls("13SIM1:", cam_suffix="cam1:")
        >>> controls.show()
    """

    widget_type: ClassVar[str] = "PVAreaDetectorControls"
    widget_description: ClassVar[str] = "AreaDetector acquisition controls"

    connection_changed = Signal(bool)
    acquisition_started = Signal()
    acquisition_stopped = Signal()

    def __init__(
        self,
        prefix: str = "",
        cam_suffix: str = "cam1:",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._prefix = prefix
        self._cam_suffix = cam_suffix
        self._cam_prefix = f"{prefix}{cam_suffix}" if prefix else ""

        self._pvs: dict[str, Any] = {}
        self._values: dict[str, Any] = {}
        self._connected_pvs: set[str] = set()
        self._was_acquiring = False

        self._setup_ui()

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
            self._cam_prefix = f"{value}{self._cam_suffix}" if value else ""
            if value:
                self._connect_pvs()

    @Property(str)
    def cam_suffix(self) -> str:
        return self._cam_suffix

    @cam_suffix.setter
    def cam_suffix(self, value: str) -> None:
        if value != self._cam_suffix:
            self._disconnect_pvs()
            self._cam_suffix = value
            self._cam_prefix = f"{self._prefix}{value}" if self._prefix else ""
            if self._prefix:
                self._connect_pvs()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(8)

        # Status Bar
        status_bar = QHBoxLayout()
        status_bar.setSpacing(8)

        self._conn_indicator = StatusIndicator()
        self._conn_indicator.set_state("disconnected")
        status_bar.addWidget(self._conn_indicator)

        self._prefix_label = QLabel(self._cam_prefix or "No prefix")
        status_bar.addWidget(self._prefix_label)

        status_bar.addStretch()

        self._state_label = QLabel("State: ---")
        self._state_label.setStyleSheet("font-weight: bold;")
        status_bar.addWidget(self._state_label)

        main_layout.addLayout(status_bar)

        # Acquisition Settings
        settings_group = QGroupBox("Acquisition")
        settings_layout = QGridLayout(settings_group)
        settings_layout.setSpacing(8)

        row = 0

        settings_layout.addWidget(QLabel("Acquire Time:"), row, 0)
        self._acquire_time_edit = PVLineEdit(
            show_units=False, precision=6, write_on_enter=True, parent=self,
        )
        settings_layout.addWidget(self._acquire_time_edit, row, 1)
        settings_layout.addWidget(QLabel("s"), row, 2)

        row += 1

        settings_layout.addWidget(QLabel("Acquire Period:"), row, 0)
        self._acquire_period_edit = PVLineEdit(
            show_units=False, precision=6, write_on_enter=True, parent=self,
        )
        settings_layout.addWidget(self._acquire_period_edit, row, 1)
        settings_layout.addWidget(QLabel("s"), row, 2)

        row += 1

        settings_layout.addWidget(QLabel("Num Images:"), row, 0)
        self._num_images_edit = PVLineEdit(
            show_units=False, write_on_enter=True, parent=self,
        )
        settings_layout.addWidget(self._num_images_edit, row, 1)

        row += 1

        settings_layout.addWidget(QLabel("Image Mode:"), row, 0)
        self._image_mode_combo = PVComboBox(write_on_change=True, parent=self)
        self._image_mode_combo.set_items(IMAGE_MODES)
        settings_layout.addWidget(self._image_mode_combo, row, 1)

        main_layout.addWidget(settings_group)

        # Control Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self._acquire_btn = QPushButton("ACQUIRE")
        self._acquire_btn.setMinimumHeight(40)
        self._acquire_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {get_success_color()};
                color: white;
                font-weight: bold;
                font-size: 12pt;
                padding: 8px 24px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #28a745;
            }}
            QPushButton:pressed {{
                background-color: #1e7e34;
            }}
            QPushButton:disabled {{
                background-color: #666666;
            }}
        """)
        self._acquire_btn.clicked.connect(self._on_acquire_clicked)
        btn_layout.addWidget(self._acquire_btn)

        self._abort_btn = QPushButton("ABORT")
        self._abort_btn.setMinimumHeight(40)
        self._abort_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {get_error_color()};
                color: white;
                font-weight: bold;
                font-size: 12pt;
                padding: 8px 24px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #ff0000;
            }}
            QPushButton:pressed {{
                background-color: #990000;
            }}
            QPushButton:disabled {{
                background-color: #666666;
            }}
        """)
        self._abort_btn.clicked.connect(self._on_abort_clicked)
        btn_layout.addWidget(self._abort_btn)

        main_layout.addLayout(btn_layout)

        # Advanced Section
        self._advanced_btn = QPushButton("> Advanced")
        self._advanced_btn.setFlat(True)
        self._advanced_btn.setCheckable(True)
        self._advanced_btn.clicked.connect(self._toggle_advanced)
        main_layout.addWidget(self._advanced_btn)

        self._advanced_group = QGroupBox()
        self._advanced_group.setVisible(False)
        advanced_layout = QGridLayout(self._advanced_group)
        advanced_layout.setSpacing(8)

        advanced_layout.addWidget(QLabel("Array Callbacks:"), 0, 0)
        self._array_callbacks_label = QLabel("---")
        advanced_layout.addWidget(self._array_callbacks_label, 0, 1)

        advanced_layout.addWidget(QLabel("Trigger Mode:"), 1, 0)
        self._trigger_mode_label = QLabel("---")
        advanced_layout.addWidget(self._trigger_mode_label, 1, 1)

        main_layout.addWidget(self._advanced_group)
        main_layout.addStretch()

        self._set_controls_enabled(False)

    def _toggle_advanced(self) -> None:
        visible = self._advanced_btn.isChecked()
        self._advanced_group.setVisible(visible)
        self._advanced_btn.setText("v Advanced" if visible else "> Advanced")

    def _connect_pvs(self) -> None:
        if not self._cam_prefix:
            return

        # Delegate acquisition settings to PV widgets
        self._acquire_time_edit.pv_name = f"{self._cam_prefix}AcquireTime"
        self._acquire_period_edit.pv_name = f"{self._cam_prefix}AcquirePeriod"
        self._num_images_edit.pv_name = f"{self._cam_prefix}NumImages"
        self._image_mode_combo.pv_name = f"{self._cam_prefix}ImageMode"

        # Manual PVs only for acquire control and detector state
        from lucid.epics.ca.pv import PV

        pv_fields = {
            "Acquire": "acquire",
            "DetectorState_RBV": "detector_state",
        }

        for field, name in pv_fields.items():
            pv_name = f"{self._cam_prefix}{field}"
            pv = PV(pv_name, parent=self)
            pv.value_changed.connect(lambda v, n=name: self._on_pv_value(n, v))
            pv.connection_changed.connect(lambda c, n=name: self._on_pv_connection(n, c))
            pv.connect_pv()
            self._pvs[name] = pv

    def _disconnect_pvs(self) -> None:
        # Disconnect PV widgets
        self._acquire_time_edit.pv_name = ""
        self._acquire_period_edit.pv_name = ""
        self._num_images_edit.pv_name = ""
        self._image_mode_combo.pv_name = ""

        # Disconnect manual PVs
        for pv in self._pvs.values():
            pv.disconnect_pv()
            pv.deleteLater()
        self._pvs.clear()
        self._connected_pvs.clear()
        self._values.clear()
        self._conn_indicator.set_state("disconnected")

    @Slot(str, object)
    def _on_pv_value(self, name: str, value: Any) -> None:
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]
        self._values[name] = value

        if name == "acquire":
            self._update_acquire_state()
        elif name == "detector_state":
            self._update_detector_state()

    @Slot(str, bool)
    def _on_pv_connection(self, name: str, connected: bool) -> None:
        if connected:
            self._connected_pvs.add(name)
        else:
            self._connected_pvs.discard(name)

        essential = {"acquire", "detector_state"}
        is_connected = essential.issubset(self._connected_pvs)

        if is_connected:
            self._conn_indicator.set_state("on")
        else:
            self._conn_indicator.set_state("disconnected")

        self._set_controls_enabled(is_connected)
        self.connection_changed.emit(is_connected)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._acquire_time_edit.readonly = not enabled
        self._acquire_period_edit.readonly = not enabled
        self._num_images_edit.readonly = not enabled
        self._image_mode_combo.readonly = not enabled
        self._acquire_btn.setEnabled(enabled)
        self._abort_btn.setEnabled(enabled)

    def _update_acquire_state(self) -> None:
        acquiring = bool(self._values.get("acquire", 0))
        if acquiring and not self._was_acquiring:
            self.acquisition_started.emit()
        elif not acquiring and self._was_acquiring:
            self.acquisition_stopped.emit()
        self._was_acquiring = acquiring

    def _update_detector_state(self) -> None:
        state = self._values.get("detector_state", 9)
        if isinstance(state, (list, tuple)):
            state = state[0] if state else 9
        state_name = DETECTOR_STATES.get(int(state), f"Unknown ({state})")
        self._state_label.setText(f"State: {state_name}")
        if state == 0:
            self._conn_indicator.set_state("on")
        elif state in (1, 2, 3, 4, 7, 8):
            self._conn_indicator.set_state("warning")
        elif state in (5, 6, 10):
            self._conn_indicator.set_state("error")
        else:
            self._conn_indicator.set_state("disconnected")

    def _on_acquire_clicked(self) -> None:
        if "acquire" in self._pvs:
            self._pvs["acquire"].put(1)

    def _on_abort_clicked(self) -> None:
        if "acquire" in self._pvs:
            self._pvs["acquire"].put(0)

    def acquire(self) -> None:
        self._on_acquire_clicked()

    def abort(self) -> None:
        self._on_abort_clicked()

    def set_acquire_time(self, seconds: float) -> None:
        if self._acquire_time_edit.connected:
            self._acquire_time_edit.write_value(seconds)

    def set_acquire_period(self, seconds: float) -> None:
        if self._acquire_period_edit.connected:
            self._acquire_period_edit.write_value(seconds)

    def set_num_images(self, count: int) -> None:
        if self._num_images_edit.connected:
            self._num_images_edit.write_value(count)

    def set_image_mode(self, mode: str) -> None:
        if mode in IMAGE_MODES:
            idx = IMAGE_MODES.index(mode)
            if self._image_mode_combo.connected:
                self._image_mode_combo.write_value(idx)

    @property
    def is_connected(self) -> bool:
        essential = {"acquire", "detector_state"}
        return essential.issubset(self._connected_pvs)

    @property
    def is_acquiring(self) -> bool:
        return bool(self._values.get("acquire", 0))

    @property
    def detector_state(self) -> str:
        state = self._values.get("detector_state", 9)
        if isinstance(state, (list, tuple)):
            state = state[0] if state else 9
        return DETECTOR_STATES.get(int(state), f"Unknown ({state})")

    @property
    def acquire_time(self) -> float | None:
        val = self._acquire_time_edit._value
        return float(val) if val is not None else None

    @property
    def acquire_period(self) -> float | None:
        val = self._acquire_period_edit._value
        return float(val) if val is not None else None

    @property
    def num_images(self) -> int | None:
        val = self._num_images_edit._value
        return int(val) if val is not None else None

    @property
    def image_mode(self) -> str | None:
        val = self._image_mode_combo._value
        if val is not None:
            idx = int(val)
            if 0 <= idx < len(IMAGE_MODES):
                return IMAGE_MODES[idx]
        return None

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "widget_type": self.widget_type,
            "widget_description": self.widget_description,
            "prefix": self._prefix,
            "cam_prefix": self._cam_prefix,
            "connected": self.is_connected,
            "connected_pvs": list(self._connected_pvs),
            "detector_state": self.detector_state,
            "is_acquiring": self.is_acquiring,
            "acquire_time": self.acquire_time,
            "acquire_period": self.acquire_period,
            "num_images": self.num_images,
            "image_mode": self.image_mode,
            "available_actions": [
                {"name": "acquire", "description": "Start acquisition"},
                {"name": "abort", "description": "Stop acquisition"},
                {"name": "set_acquire_time", "args": ["seconds"], "description": "Set exposure time"},
                {"name": "set_acquire_period", "args": ["seconds"], "description": "Set period between images"},
                {"name": "set_num_images", "args": ["count"], "description": "Set number of images"},
                {"name": "set_image_mode", "args": ["mode"], "description": "Set image mode"},
            ],
        }

    def closeEvent(self, event) -> None:
        self._disconnect_pvs()
        super().closeEvent(event)
