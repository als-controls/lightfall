"""Compact motor control widget for favorites display.

Provides a single horizontal row with motor name, readback,
jog/abs toggle, setpoint entry, go button, and stop button.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QWidget,
)

from lucid.epics.widgets.ophyd_label import OphydLabel
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.devices.model import DeviceInfo


class CompactMotorWidget(QWidget):
    """Compact single-row motor control widget.

    Layout (left to right):
        Name | Readback | Jog/Abs toggle | Setpoint | Go | Stop

    Signals:
        open_controller_requested: Emitted with device_id when user wants full controller tab.
        remove_favorite_requested: Emitted with device_id when user wants to unfavorite.
        control_error: Emitted with error message string.
    """

    open_controller_requested = Signal(str)
    remove_favorite_requested = Signal(str)
    control_error = Signal(str)

    WIDGET_HEIGHT = 38

    def __init__(
        self,
        device_info: DeviceInfo,
        ophyd_obj: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._device_info = device_info
        self._motor = ophyd_obj
        self._is_jog_mode = False

        self.setFixedHeight(self.WIDGET_HEIGHT)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._setup_ui()
        self._bind_signals()
        self._update_state()

    @property
    def device_id(self) -> str:
        return str(self._device_info.id)

    @property
    def is_jog_mode(self) -> bool:
        return self._is_jog_mode

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        self._name_label = QLabel(self._device_info.name)
        self._name_label.setStyleSheet("font-weight: bold;")
        self._name_label.setFixedWidth(120)
        self._name_label.setToolTip(self._device_info.name)
        layout.addWidget(self._name_label)

        precision = 4
        if self._device_info.metadata:
            precision = self._device_info.metadata.get("precision", 4)
        self._rbv_display = OphydLabel(precision=precision)
        self._rbv_display._value_label.setStyleSheet(
            "font-family: monospace; font-size: 10pt;"
        )
        self._rbv_display._value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._rbv_display.setMinimumWidth(80)
        layout.addWidget(self._rbv_display)

        self._mode_btn = QPushButton("Abs")
        self._mode_btn.setFixedWidth(40)
        self._mode_btn.setToolTip("Toggle between Absolute and Jog (relative) mode")
        self._mode_btn.setCheckable(True)
        self._mode_btn.clicked.connect(self._on_mode_toggled)
        layout.addWidget(self._mode_btn)

        self._setpoint_edit = QLineEdit()
        self._setpoint_edit.setValidator(QDoubleValidator())
        self._setpoint_edit.setPlaceholderText("Target")
        self._setpoint_edit.setMaximumWidth(100)
        self._setpoint_edit.returnPressed.connect(self._on_go_clicked)
        layout.addWidget(self._setpoint_edit)

        self._go_btn = QPushButton("Go")
        self._go_btn.setFixedWidth(36)
        self._go_btn.clicked.connect(self._on_go_clicked)
        layout.addWidget(self._go_btn)

        self._stop_btn = QPushButton("\u25a0")
        self._stop_btn.setFixedWidth(30)
        self._stop_btn.setToolTip("Stop motor")
        self._stop_btn.setStyleSheet("color: #F44336; font-weight: bold;")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self._stop_btn)

    def _bind_signals(self) -> None:
        if self._motor is None:
            return
        if hasattr(self._motor, "user_readback"):
            self._rbv_display.signal = self._motor.user_readback
        elif hasattr(self._motor, "readback"):
            self._rbv_display.signal = self._motor.readback

    def _unbind_signals(self) -> None:
        self._rbv_display.signal = None

    def _update_state(self) -> None:
        has_motor = self._motor is not None
        self._go_btn.setEnabled(has_motor)
        self._stop_btn.setEnabled(has_motor)
        self._setpoint_edit.setEnabled(has_motor)
        self._mode_btn.setEnabled(has_motor)
        if not has_motor:
            self._rbv_display._value_label.setText("...")
            self._name_label.setText(f"{self._device_info.name} (connecting...)")

    def set_motor(self, ophyd_obj: Any) -> None:
        """Update the underlying motor object (e.g. after delayed connection)."""
        self._unbind_signals()
        self._motor = ophyd_obj
        self._bind_signals()
        self._name_label.setText(self._device_info.name)
        self._update_state()

    def _get_current_position(self) -> float | None:
        if self._motor is None:
            return None
        if hasattr(self._motor, "position"):
            return self._motor.position
        if hasattr(self._motor, "readback") and hasattr(self._motor.readback, "get"):
            return self._motor.readback.get()
        return None

    @Slot(bool)
    def _on_mode_toggled(self, checked: bool) -> None:
        self._is_jog_mode = checked
        if checked:
            self._mode_btn.setText("Jog")
            self._setpoint_edit.setPlaceholderText("Step")
        else:
            self._mode_btn.setText("Abs")
            self._setpoint_edit.setPlaceholderText("Target")

    @Slot()
    def _on_go_clicked(self) -> None:
        if self._motor is None:
            return
        text = self._setpoint_edit.text().strip()
        if not text:
            return
        try:
            value = float(text)
            if self._is_jog_mode:
                current = self._get_current_position()
                if current is None:
                    self.control_error.emit("Cannot read current position for jog")
                    return
                target = current + value
            else:
                target = value
            if hasattr(self._motor, "set"):
                self._motor.set(target)
                logger.info(
                    "CompactMotor: {} {} to {}",
                    "Jog" if self._is_jog_mode else "Move",
                    self._device_info.name,
                    target,
                )
        except ValueError:
            self.control_error.emit("Invalid value")
        except Exception as e:
            self.control_error.emit(f"Move failed: {e}")
            logger.error("CompactMotor move failed: {}", e)

    @Slot()
    def _on_stop_clicked(self) -> None:
        if self._motor is None:
            return
        try:
            if hasattr(self._motor, "stop"):
                self._motor.stop()
                logger.info("CompactMotor: stopped {}", self._device_info.name)
        except Exception as e:
            self.control_error.emit(f"Stop failed: {e}")
            logger.error("CompactMotor stop failed: {}", e)

    @Slot(QPoint)
    def _on_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("Open Controller")
        open_action.triggered.connect(
            lambda: self.open_controller_requested.emit(self.device_id)
        )
        remove_action = menu.addAction("Remove from Favorites")
        remove_action.triggered.connect(
            lambda: self.remove_favorite_requested.emit(self.device_id)
        )
        menu.exec(self.mapToGlobal(pos))

    def closeEvent(self, event) -> None:
        self._unbind_signals()
        super().closeEvent(event)
