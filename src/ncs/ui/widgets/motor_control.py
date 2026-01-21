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
    QAbstractItemView,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ncs.devices.model import DeviceCategory
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
            if hasattr(self._motor, "set"):
                self.motion_started.emit(self._motor_name)
                status = self._motor.set(target)
                # Optionally wait or handle async
                logger.info("Moving {} to {}", self._motor_name, target)
            elif hasattr(self._motor, "move"):
                self.motion_started.emit(self._motor_name)
                self._motor.move(target)
                logger.info("Moving {} to {}", self._motor_name, target)
        except ValueError:
            self.control_error.emit("Invalid setpoint value")
        except Exception as e:
            self.control_error.emit(f"Move failed: {e}")
            logger.error("Motor move failed: {}", e)

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
            current = None

            if hasattr(self._motor, "position"):
                current = self._motor.position
            elif hasattr(self._motor, "readback") and hasattr(self._motor.readback, "get"):
                current = self._motor.readback.get()

            if current is not None:
                target = current + (direction * step)
                if hasattr(self._motor, "set"):
                    self.motion_started.emit(self._motor_name)
                    self._motor.set(target)
                    logger.info("Tweaking {} to {}", self._motor_name, target)
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
                self.motion_finished.emit(self._motor_name)
        except Exception as e:
            self.control_error.emit(f"Stop failed: {e}")
            logger.error("Motor stop failed: {}", e)

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        self._stop_updates()
        super().closeEvent(event)


@register_control_widget
class MultiMotorControlWidget(BaseControlWidget):
    """Control widget for multiple motor devices.

    Displays a table of motors with:
    - Name, Position, Setpoint columns
    - Individual Go buttons per motor
    - Shared tweak controls for synchronized relative motion
    - Global Stop All button
    """

    display_name: ClassVar[str] = "Multi-Motor Control"
    priority: ClassVar[int] = 90  # Slightly lower than single motor

    def __init__(self, parent: QWidget | None = None) -> None:
        self._motors: list[tuple[str, Any, DeviceTreeItem]] = []  # (name, ophyd_obj, item)
        self._update_timer: QTimer | None = None
        super().__init__(parent)

    @classmethod
    def can_control(cls, items: list[DeviceTreeItem]) -> bool:
        """Check if this widget can control the given items."""
        # Can control two or more motors
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

        self._rebuild_table()
        if self._motors:
            self._start_updates()
        else:
            self._stop_updates()

    def _setup_ui(self) -> None:
        """Setup the multi-motor control UI."""
        # Header
        header_label = QLabel("Multi-Motor Control")
        header_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self._layout.addWidget(header_label)

        # Motor table
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["Name", "Position", "Setpoint", "", "Status"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setColumnWidth(3, 50)

        self._layout.addWidget(self._table)

        # Tweak controls
        tweak_group = QGroupBox("Synchronized Relative Motion")
        tweak_layout = QHBoxLayout(tweak_group)

        self._twr_btn = QPushButton("\u25C0 All")
        self._twr_btn.setToolTip("Move all motors negative")
        self._twr_btn.clicked.connect(self._on_tweak_all_reverse)
        tweak_layout.addWidget(self._twr_btn)

        self._tweak_edit = QLineEdit("1.0")
        self._tweak_edit.setValidator(QDoubleValidator(0.0001, 1000000, 6))
        self._tweak_edit.setToolTip("Step size for all motors")
        self._tweak_edit.setMaximumWidth(80)
        tweak_layout.addWidget(self._tweak_edit)

        self._twf_btn = QPushButton("All \u25B6")
        self._twf_btn.setToolTip("Move all motors positive")
        self._twf_btn.clicked.connect(self._on_tweak_all_forward)
        tweak_layout.addWidget(self._twf_btn)

        tweak_layout.addStretch()
        self._layout.addWidget(tweak_group)

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

    def _rebuild_table(self) -> None:
        """Rebuild the motor table."""
        self._table.setRowCount(len(self._motors))
        self._setpoint_edits: list[QLineEdit] = []
        self._go_buttons: list[QPushButton] = []

        for row, (name, motor, item) in enumerate(self._motors):
            # Name
            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, name_item)

            # Position (updated periodically)
            pos_item = QTableWidgetItem("---")
            pos_item.setFlags(pos_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, pos_item)

            # Setpoint edit
            setpoint_edit = QLineEdit()
            setpoint_edit.setValidator(QDoubleValidator())
            setpoint_edit.setPlaceholderText("Target")
            self._setpoint_edits.append(setpoint_edit)
            self._table.setCellWidget(row, 2, setpoint_edit)

            # Go button
            go_btn = QPushButton("Go")
            go_btn.clicked.connect(lambda checked, r=row: self._on_go_row(r))
            self._go_buttons.append(go_btn)
            self._table.setCellWidget(row, 3, go_btn)

            # Status
            status_item = QTableWidgetItem("Idle")
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 4, status_item)

    def _start_updates(self) -> None:
        """Start periodic updates."""
        if self._update_timer is None:
            self._update_timer = QTimer(self)
            self._update_timer.timeout.connect(self._update_table)
        self._update_timer.start(200)

    def _stop_updates(self) -> None:
        """Stop periodic updates."""
        if self._update_timer is not None:
            self._update_timer.stop()

    def _update_table(self) -> None:
        """Update position and status columns."""
        for row, (name, motor, item) in enumerate(self._motors):
            try:
                # Position
                pos = None
                if hasattr(motor, "position"):
                    pos = motor.position
                elif hasattr(motor, "readback") and hasattr(motor.readback, "get"):
                    pos = motor.readback.get()

                if pos is not None:
                    precision = 4
                    if item.device_info and item.device_info.metadata:
                        precision = item.device_info.metadata.get("precision", 4)
                    pos_item = self._table.item(row, 1)
                    if pos_item:
                        pos_item.setText(f"{pos:.{precision}f}")

                # Status
                is_moving = False
                if hasattr(motor, "moving"):
                    is_moving = bool(motor.moving)

                status_item = self._table.item(row, 4)
                if status_item:
                    status_item.setText("Moving" if is_moving else "Idle")

            except Exception as e:
                logger.warning("Error updating motor {}: {}", name, e)

    @Slot()
    def _on_go_row(self, row: int) -> None:
        """Move a single motor to its setpoint."""
        if row >= len(self._motors):
            return

        name, motor, item = self._motors[row]
        setpoint_edit = self._setpoint_edits[row]

        try:
            target = float(setpoint_edit.text())
            if hasattr(motor, "set"):
                self.motion_started.emit(name)
                motor.set(target)
                logger.info("Moving {} to {}", name, target)
        except ValueError:
            self.control_error.emit(f"Invalid setpoint for {name}")
        except Exception as e:
            self.control_error.emit(f"Move failed for {name}: {e}")
            logger.error("Motor move failed for {}: {}", name, e)

    @Slot()
    def _on_tweak_all_forward(self) -> None:
        """Tweak all motors forward."""
        self._tweak_all(1)

    @Slot()
    def _on_tweak_all_reverse(self) -> None:
        """Tweak all motors reverse."""
        self._tweak_all(-1)

    def _tweak_all(self, direction: int) -> None:
        """Move all motors by tweak amount."""
        try:
            step = float(self._tweak_edit.text())
        except ValueError:
            self.control_error.emit("Invalid tweak value")
            return

        for name, motor, item in self._motors:
            try:
                current = None
                if hasattr(motor, "position"):
                    current = motor.position
                elif hasattr(motor, "readback") and hasattr(motor.readback, "get"):
                    current = motor.readback.get()

                if current is not None:
                    target = current + (direction * step)
                    if hasattr(motor, "set"):
                        self.motion_started.emit(name)
                        motor.set(target)
            except Exception as e:
                logger.error("Tweak failed for {}: {}", name, e)

        logger.info("Tweaked all motors by {}", direction * step)

    @Slot()
    def _on_stop_all(self) -> None:
        """Stop all motors."""
        for name, motor, item in self._motors:
            try:
                if hasattr(motor, "stop"):
                    motor.stop()
                    self.motion_finished.emit(name)
            except Exception as e:
                logger.error("Stop failed for {}: {}", name, e)

        logger.info("Stopped all motors")

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        self._stop_updates()
        super().closeEvent(event)
