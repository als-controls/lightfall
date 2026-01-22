"""Motor control widgets for direct device control.

Provides control UIs for ophyd motor devices:
- MotorControlWidget: Single motor control with full feature set
- MultiMotorControlWidget: Control multiple motors together
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ncs.devices.model import DeviceCategory
from ncs.logbook import DeviceActionLogger
from ncs.ui.models.device_tree import DeviceTreeItem, NodeType
from ncs.ui.widgets.base_control import BaseControlWidget, register_control_widget
from ncs.utils.logging import logger

if TYPE_CHECKING:
    pass


def is_motor_item(item: DeviceTreeItem) -> bool:
    """Check if a DeviceTreeItem represents a motor device.

    Args:
        item: The tree item to check.

    Returns:
        True if the item is a motor device.
    """
    # Must be a device node (not signal or component)
    if item.node_type != NodeType.DEVICE:
        return False

    # Check device category from device_info
    if item.device_info and item.device_info.category == DeviceCategory.MOTOR:
        return True

    # Check ophyd object class name
    if item.ophyd_obj is not None:
        class_name = type(item.ophyd_obj).__name__.lower()
        if any(kw in class_name for kw in ("motor", "axis", "positioner")):
            return True

    return False


class StatusIndicator(QWidget):
    """Small colored indicator for status display."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._state = "off"
        self._update_style()

    def set_state(self, state: str) -> None:
        """Set indicator state: 'off', 'on', 'warning', 'error'."""
        self._state = state
        self._update_style()

    def _update_style(self) -> None:
        colors = {
            "off": "#666666",
            "on": "#4CAF50",
            "warning": "#FFC107",
            "error": "#F44336",
        }
        color = colors.get(self._state, colors["off"])
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {color};
                border-radius: 6px;
                border: 1px solid #333;
            }}
        """)


@register_control_widget
class MotorControlWidget(BaseControlWidget):
    """Control widget for a single motor device.

    Provides:
    - Position readback display
    - Setpoint entry with Go button
    - Tweak buttons for relative motion
    - Stop button
    - Status indicators (moving, limits)
    - Advanced section (velocity, acceleration)

    Works directly with ophyd motor devices using their native interface.
    """

    display_name: ClassVar[str] = "Motor Control"
    priority: ClassVar[int] = 100  # High priority for motors

    def __init__(self, parent: QWidget | None = None) -> None:
        self._motor: Any = None
        self._motor_name: str = ""
        self._units: str = ""
        self._precision: int = 4
        self._update_timer: QTimer | None = None
        super().__init__(parent)

    @classmethod
    def can_control(cls, items: list[DeviceTreeItem]) -> bool:
        """Check if this widget can control the given items."""
        # Can control exactly one motor
        return len(items) == 1 and is_motor_item(items[0])

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the motor to control."""
        self._items = items

        if items and len(items) == 1:
            item = items[0]
            self._motor = item.ophyd_obj
            self._motor_name = item.name

            # Get units and precision from metadata
            if item.device_info and item.device_info.metadata:
                self._units = item.device_info.metadata.get("units", "")
                self._precision = item.device_info.metadata.get("precision", 4)

            self._update_display()
            self._start_updates()
        else:
            self._motor = None
            self._motor_name = ""
            self._stop_updates()
            self._clear_display()

    def _setup_ui(self) -> None:
        """Setup the motor control UI."""
        # Motor name header
        self._name_label = QLabel("No Motor Selected")
        self._name_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self._layout.addWidget(self._name_label)

        # Status bar
        status_layout = QHBoxLayout()

        # Moving indicator
        self._moving_indicator = StatusIndicator()
        self._moving_label = QLabel("Idle")
        status_layout.addWidget(self._moving_indicator)
        status_layout.addWidget(self._moving_label)
        status_layout.addStretch()

        self._layout.addLayout(status_layout)

        # Position group
        pos_group = QGroupBox("Position")
        pos_layout = QGridLayout(pos_group)

        # Readback display
        pos_layout.addWidget(QLabel("Current:"), 0, 0)
        self._rbv_display = QLabel("---")
        self._rbv_display.setStyleSheet("""
            QLabel {
                font-size: 16pt;
                font-weight: bold;
                font-family: monospace;
                padding: 4px 8px;
            }
        """)
        pos_layout.addWidget(self._rbv_display, 0, 1)
        self._units_label = QLabel("")
        pos_layout.addWidget(self._units_label, 0, 2)

        # Setpoint entry
        pos_layout.addWidget(QLabel("Setpoint:"), 1, 0)
        self._setpoint_edit = QLineEdit()
        self._setpoint_edit.setValidator(QDoubleValidator())
        self._setpoint_edit.setPlaceholderText("Enter position")
        self._setpoint_edit.returnPressed.connect(self._on_go_clicked)
        pos_layout.addWidget(self._setpoint_edit, 1, 1)

        self._go_btn = QPushButton("Go")
        self._go_btn.setFixedWidth(50)
        self._go_btn.clicked.connect(self._on_go_clicked)
        pos_layout.addWidget(self._go_btn, 1, 2)

        self._layout.addWidget(pos_group)

        # Tweak controls
        tweak_group = QGroupBox("Relative Motion")
        tweak_layout = QHBoxLayout(tweak_group)

        self._twr_btn = QPushButton("\u25C0")  # Left arrow
        self._twr_btn.setToolTip("Move negative")
        self._twr_btn.setFixedWidth(40)
        self._twr_btn.clicked.connect(self._on_tweak_reverse)
        tweak_layout.addWidget(self._twr_btn)

        self._tweak_edit = QLineEdit("1.0")
        self._tweak_edit.setValidator(QDoubleValidator(0.0001, 1000000, 6))
        self._tweak_edit.setToolTip("Step size")
        self._tweak_edit.setMaximumWidth(80)
        tweak_layout.addWidget(self._tweak_edit)

        self._twf_btn = QPushButton("\u25B6")  # Right arrow
        self._twf_btn.setToolTip("Move positive")
        self._twf_btn.setFixedWidth(40)
        self._twf_btn.clicked.connect(self._on_tweak_forward)
        tweak_layout.addWidget(self._twf_btn)

        tweak_layout.addStretch()
        self._layout.addWidget(tweak_group)

        # Stop button
        btn_layout = QHBoxLayout()
        self._stop_btn = QPushButton("STOP")
        self._stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #ff0000;
            }
            QPushButton:pressed {
                background-color: #990000;
            }
        """)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        btn_layout.addWidget(self._stop_btn)
        btn_layout.addStretch()
        self._layout.addLayout(btn_layout)

        self._layout.addStretch()

        # Initial state - disabled until motor is set
        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable control widgets."""
        self._setpoint_edit.setEnabled(enabled)
        self._go_btn.setEnabled(enabled)
        self._twf_btn.setEnabled(enabled)
        self._twr_btn.setEnabled(enabled)
        self._tweak_edit.setEnabled(enabled)
        self._stop_btn.setEnabled(enabled)

    def _start_updates(self) -> None:
        """Start periodic position updates."""
        if self._update_timer is None:
            self._update_timer = QTimer(self)
            self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(200)  # 5 Hz updates

    def _stop_updates(self) -> None:
        """Stop periodic position updates."""
        if self._update_timer is not None:
            self._update_timer.stop()

    def _update_display(self) -> None:
        """Update the position and status display."""
        if self._motor is None:
            return

        self._name_label.setText(self._motor_name)
        self._units_label.setText(self._units)
        self._set_controls_enabled(True)

        try:
            # Get current position
            pos = None
            if hasattr(self._motor, "position"):
                pos = self._motor.position
            elif hasattr(self._motor, "readback") and hasattr(self._motor.readback, "get"):
                pos = self._motor.readback.get()

            if pos is not None:
                self._rbv_display.setText(f"{pos:.{self._precision}f}")

            # Update moving status
            is_moving = False
            if hasattr(self._motor, "moving"):
                is_moving = bool(self._motor.moving)

            if is_moving:
                self._moving_indicator.set_state("on")
                self._moving_label.setText("Moving")
            else:
                self._moving_indicator.set_state("off")
                self._moving_label.setText("Idle")

        except Exception as e:
            logger.warning("Error updating motor display: {}", e)

    def _clear_display(self) -> None:
        """Clear the display when no motor is selected."""
        self._name_label.setText("No Motor Selected")
        self._rbv_display.setText("---")
        self._units_label.setText("")
        self._moving_indicator.set_state("off")
        self._moving_label.setText("Idle")
        self._set_controls_enabled(False)

    @Slot()
    def _on_go_clicked(self) -> None:
        """Move motor to setpoint."""
        if self._motor is None:
            return

        try:
            target = float(self._setpoint_edit.text())
            current = self._get_current_position()
            self._start_move(target, current)
        except ValueError:
            self.control_error.emit("Invalid setpoint value")
        except Exception as e:
            self.control_error.emit(f"Move failed: {e}")
            logger.error("Motor move failed: {}", e)

    def _get_current_position(self) -> float | None:
        """Get the current motor position."""
        if self._motor is None:
            return None
        if hasattr(self._motor, "position"):
            return self._motor.position
        if hasattr(self._motor, "readback") and hasattr(self._motor.readback, "get"):
            return self._motor.readback.get()
        return None

    def _start_move(self, target: float, start_position: float | None = None) -> None:
        """Start a motor move with completion tracking.

        Args:
            target: Target position.
            start_position: Starting position (for logging).
        """
        if self._motor is None:
            return

        motor_name = self._motor_name

        # Record move start with values for action logging
        action_logger = DeviceActionLogger.get_instance()
        action_logger.record_move_start(
            device_name=motor_name,
            old_value=start_position,
            target_value=target,
            unit=self._units,
        )

        self.motion_started.emit(motor_name)

        if hasattr(self._motor, "set"):
            status = self._motor.set(target)
            logger.info("Moving {} to {}", motor_name, target)

            # Add completion callback
            def on_complete(status=None):
                # Emit signal with move details for action logging
                self.motion_finished.emit(motor_name)

            if hasattr(status, "add_callback"):
                status.add_callback(on_complete)
            elif hasattr(status, "finished"):
                # Alternative: check if it has a finished signal
                status.finished.connect(on_complete)
            else:
                # No async tracking available, emit immediately
                on_complete()

        elif hasattr(self._motor, "move"):
            self._motor.move(target)
            logger.info("Moving {} to {}", motor_name, target)
            # Synchronous move, emit completion
            self.motion_finished.emit(motor_name)

    @Slot()
    def _on_tweak_forward(self) -> None:
        """Move motor forward by tweak amount."""
        self._do_relative_move(1)

    @Slot()
    def _on_tweak_reverse(self) -> None:
        """Move motor reverse by tweak amount."""
        self._do_relative_move(-1)

    def _do_relative_move(self, direction: int) -> None:
        """Perform a relative move."""
        if self._motor is None:
            return

        try:
            step = float(self._tweak_edit.text())
            current = self._get_current_position()

            if current is not None:
                target = current + (direction * step)
                self._start_move(target, current)
        except ValueError:
            self.control_error.emit("Invalid tweak value")
        except Exception as e:
            self.control_error.emit(f"Tweak failed: {e}")
            logger.error("Motor tweak failed: {}", e)

    @Slot()
    def _on_stop_clicked(self) -> None:
        """Stop the motor."""
        if self._motor is None:
            return

        try:
            if hasattr(self._motor, "stop"):
                self._motor.stop()
                logger.info("Stopped motor {}", self._motor_name)

                # Clear any pending move and record a stop action instead
                action_logger = DeviceActionLogger.get_instance()
                action_logger._pending_moves.pop(self._motor_name, None)
                action_logger.record_action(
                    device_name=self._motor_name,
                    action_type="stop",
                )
        except Exception as e:
            self.control_error.emit(f"Stop failed: {e}")
            logger.error("Motor stop failed: {}", e)

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        self._stop_updates()
        super().closeEvent(event)


class MotorRowWidget(QWidget):
    """Individual motor row for multi-motor control."""

    move_requested = Signal(str, float, bool)  # name, value, is_relative
    stop_requested = Signal(str)  # name

    def __init__(
        self,
        name: str,
        motor: Any,
        item: DeviceTreeItem,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.name = name
        self.motor = motor
        self.item = item
        self._precision = 4
        if item.device_info and item.device_info.metadata:
            self._precision = item.device_info.metadata.get("precision", 4)

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # Motor name
        self._name_label = QLabel(self.name)
        self._name_label.setStyleSheet("font-weight: bold;")
        self._name_label.setMinimumWidth(80)
        layout.addWidget(self._name_label)

        # Position display
        self._pos_label = QLabel("---")
        self._pos_label.setStyleSheet("font-family: monospace; font-size: 11pt;")
        self._pos_label.setMinimumWidth(90)
        self._pos_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._pos_label)

        # Status indicator
        self._status_indicator = StatusIndicator()
        layout.addWidget(self._status_indicator)

        # Spacer
        layout.addSpacing(16)

        # Tweak reverse button
        self._twr_btn = QPushButton("\u25C0")
        self._twr_btn.setFixedWidth(30)
        self._twr_btn.setToolTip("Move negative")
        self._twr_btn.clicked.connect(self._on_tweak_reverse)
        layout.addWidget(self._twr_btn)

        # Value entry (setpoint or step size depending on mode)
        self._value_edit = QLineEdit()
        self._value_edit.setValidator(QDoubleValidator())
        self._value_edit.setMaximumWidth(100)
        self._value_edit.returnPressed.connect(self._on_go)
        layout.addWidget(self._value_edit)

        # Tweak forward button
        self._twf_btn = QPushButton("\u25B6")
        self._twf_btn.setFixedWidth(30)
        self._twf_btn.setToolTip("Move positive")
        self._twf_btn.clicked.connect(self._on_tweak_forward)
        layout.addWidget(self._twf_btn)

        # Go button
        self._go_btn = QPushButton("Go")
        self._go_btn.setFixedWidth(40)
        self._go_btn.clicked.connect(self._on_go)
        layout.addWidget(self._go_btn)

        # Stop button
        self._stop_btn = QPushButton("\u25A0")
        self._stop_btn.setFixedWidth(30)
        self._stop_btn.setToolTip("Stop")
        self._stop_btn.setStyleSheet("color: #F44336; font-weight: bold;")
        self._stop_btn.clicked.connect(self._on_stop)
        layout.addWidget(self._stop_btn)

    def set_mode(self, is_relative: bool) -> None:
        """Set the movement mode (absolute or relative)."""
        self._is_relative = is_relative
        if is_relative:
            self._value_edit.setPlaceholderText("Step")
            self._go_btn.setVisible(False)
            self._twr_btn.setVisible(True)
            self._twf_btn.setVisible(True)
        else:
            self._value_edit.setPlaceholderText("Target")
            self._go_btn.setVisible(True)
            self._twr_btn.setVisible(False)
            self._twf_btn.setVisible(False)

    def update_display(self) -> None:
        """Update position and status display."""
        try:
            pos = None
            if hasattr(self.motor, "position"):
                pos = self.motor.position
            elif hasattr(self.motor, "readback") and hasattr(self.motor.readback, "get"):
                pos = self.motor.readback.get()

            if pos is not None:
                self._pos_label.setText(f"{pos:.{self._precision}f}")

            is_moving = False
            if hasattr(self.motor, "moving"):
                is_moving = bool(self.motor.moving)

            self._status_indicator.set_state("on" if is_moving else "off")
        except Exception:
            pass

    @Slot()
    def _on_go(self) -> None:
        """Handle Go button - absolute move."""
        try:
            value = float(self._value_edit.text())
            self.move_requested.emit(self.name, value, False)
        except ValueError:
            pass

    @Slot()
    def _on_tweak_forward(self) -> None:
        """Handle tweak forward - relative move positive."""
        try:
            value = float(self._value_edit.text())
            self.move_requested.emit(self.name, value, True)
        except ValueError:
            pass

    @Slot()
    def _on_tweak_reverse(self) -> None:
        """Handle tweak reverse - relative move negative."""
        try:
            value = float(self._value_edit.text())
            self.move_requested.emit(self.name, -value, True)
        except ValueError:
            pass

    @Slot()
    def _on_stop(self) -> None:
        """Handle stop button."""
        self.stop_requested.emit(self.name)


@register_control_widget
class MultiMotorControlWidget(BaseControlWidget):
    """Control widget for multiple motor devices.

    Displays individual motor controls with:
    - Position display and status indicator per motor
    - Toggle between Absolute and Relative movement modes
    - Absolute mode: setpoint entry with Go button
    - Relative mode: step size with +/- tweak buttons
    - Global Stop All button
    """

    display_name: ClassVar[str] = "Multi-Motor Control"
    priority: ClassVar[int] = 90  # Slightly lower than single motor

    def __init__(self, parent: QWidget | None = None) -> None:
        self._motors: list[tuple[str, Any, DeviceTreeItem]] = []
        self._motor_rows: list[MotorRowWidget] = []
        self._update_timer: QTimer | None = None
        self._is_relative_mode = False
        super().__init__(parent)

    @classmethod
    def can_control(cls, items: list[DeviceTreeItem]) -> bool:
        """Check if this widget can control the given items."""
        if len(items) < 2:
            return False
        return all(is_motor_item(item) for item in items)

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the motors to control."""
        self._items = items
        self._motors = []

        for item in items:
            if is_motor_item(item) and item.ophyd_obj is not None:
                self._motors.append((item.name, item.ophyd_obj, item))

        self._rebuild_motor_list()
        if self._motors:
            self._start_updates()
        else:
            self._stop_updates()

    def _setup_ui(self) -> None:
        """Setup the multi-motor control UI."""
        # Header with mode toggle
        header_layout = QHBoxLayout()

        header_label = QLabel("Multi-Motor Control")
        header_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        header_layout.addWidget(header_label)

        header_layout.addStretch()

        # Mode toggle button
        self._mode_btn = QPushButton("Absolute")
        self._mode_btn.setCheckable(True)
        self._mode_btn.setToolTip("Toggle between Absolute and Relative movement modes")
        self._mode_btn.clicked.connect(self._on_mode_toggled)
        self._mode_btn.setStyleSheet("""
            QPushButton {
                padding: 4px 12px;
                border: 1px solid #666;
                border-radius: 4px;
            }
            QPushButton:checked {
                background-color: #4CAF50;
                color: white;
                border-color: #4CAF50;
            }
        """)
        header_layout.addWidget(self._mode_btn)

        self._layout.addLayout(header_layout)

        # Container for motor rows
        self._motors_container = QWidget()
        self._motors_layout = QVBoxLayout(self._motors_container)
        self._motors_layout.setContentsMargins(0, 0, 0, 0)
        self._motors_layout.setSpacing(2)
        self._layout.addWidget(self._motors_container)

        # Stop All button
        btn_layout = QHBoxLayout()
        self._stop_all_btn = QPushButton("STOP ALL")
        self._stop_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                font-weight: bold;
                padding: 8px 24px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #ff0000;
            }
            QPushButton:pressed {
                background-color: #990000;
            }
        """)
        self._stop_all_btn.clicked.connect(self._on_stop_all)
        btn_layout.addWidget(self._stop_all_btn)
        btn_layout.addStretch()
        self._layout.addLayout(btn_layout)

        self._layout.addStretch()

    def _rebuild_motor_list(self) -> None:
        """Rebuild the motor row widgets."""
        # Clear existing rows
        for row in self._motor_rows:
            row.deleteLater()
        self._motor_rows.clear()

        # Create new rows
        for name, motor, item in self._motors:
            row = MotorRowWidget(name, motor, item, self._motors_container)
            row.set_mode(self._is_relative_mode)
            row.move_requested.connect(self._on_move_requested)
            row.stop_requested.connect(self._on_stop_requested)
            self._motors_layout.addWidget(row)
            self._motor_rows.append(row)

    def _start_updates(self) -> None:
        """Start periodic updates."""
        if self._update_timer is None:
            self._update_timer = QTimer(self)
            self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(200)

    def _stop_updates(self) -> None:
        """Stop periodic updates."""
        if self._update_timer is not None:
            self._update_timer.stop()

    def _update_display(self) -> None:
        """Update all motor row displays."""
        for row in self._motor_rows:
            row.update_display()

    @Slot(bool)
    def _on_mode_toggled(self, checked: bool) -> None:
        """Handle mode toggle button."""
        self._is_relative_mode = checked
        self._mode_btn.setText("Relative" if checked else "Absolute")

        for row in self._motor_rows:
            row.set_mode(checked)

    @Slot(str, float, bool)
    def _on_move_requested(self, name: str, value: float, is_relative: bool) -> None:
        """Handle move request from a motor row."""
        # Find the motor and item
        motor = None
        item = None
        for n, m, i in self._motors:
            if n == name:
                motor = m
                item = i
                break

        if motor is None:
            return

        try:
            # Get current position
            current = None
            if hasattr(motor, "position"):
                current = motor.position
            elif hasattr(motor, "readback") and hasattr(motor.readback, "get"):
                current = motor.readback.get()

            target = value
            if is_relative:
                if current is None:
                    return
                target = current + value
                logger.info("Relative move {} by {} to {}", name, value, target)
            else:
                logger.info("Absolute move {} to {}", name, value)

            # Get units from item metadata
            unit = ""
            if item and item.device_info and item.device_info.metadata:
                unit = item.device_info.metadata.get("units", "")

            # Record move start with values for action logging
            action_logger = DeviceActionLogger.get_instance()
            action_logger.record_move_start(
                device_name=name,
                old_value=current,
                target_value=target,
                unit=unit,
            )

            # Start the move with completion tracking
            if hasattr(motor, "set"):
                self.motion_started.emit(name)
                status = motor.set(target)

                # Add completion callback
                def on_complete(status=None, motor_name=name):
                    self.motion_finished.emit(motor_name)

                if hasattr(status, "add_callback"):
                    status.add_callback(on_complete)
                elif hasattr(status, "finished"):
                    status.finished.connect(on_complete)
                else:
                    on_complete()

        except Exception as e:
            self.control_error.emit(f"Move failed for {name}: {e}")
            logger.error("Motor move failed for {}: {}", name, e)

    @Slot(str)
    def _on_stop_requested(self, name: str) -> None:
        """Handle stop request from a motor row."""
        for n, motor, item in self._motors:
            if n == name:
                try:
                    if hasattr(motor, "stop"):
                        motor.stop()
                        logger.info("Stopped motor {}", name)

                        # Clear any pending move and record a stop action
                        action_logger = DeviceActionLogger.get_instance()
                        action_logger._pending_moves.pop(name, None)
                        action_logger.record_action(
                            device_name=name,
                            action_type="stop",
                        )
                except Exception as e:
                    logger.error("Stop failed for {}: {}", name, e)
                break

    @Slot()
    def _on_stop_all(self) -> None:
        """Stop all motors."""
        action_logger = DeviceActionLogger.get_instance()

        for name, motor, item in self._motors:
            try:
                if hasattr(motor, "stop"):
                    motor.stop()

                    # Clear any pending move and record a stop action
                    action_logger._pending_moves.pop(name, None)
                    action_logger.record_action(
                        device_name=name,
                        action_type="stop",
                    )
            except Exception as e:
                logger.error("Stop failed for {}: {}", name, e)

        logger.info("Stopped all motors")

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        self._stop_updates()
        super().closeEvent(event)
