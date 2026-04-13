"""
PVMotor widget - specialized UI for EPICS motor records.

Provides a comprehensive motor control interface with:
- Absolute and relative positioning
- Setpoint and readback display
- Status indicators (moving, limits, direction)
- Stop and enable controls
- Progressive disclosure with advanced section
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Property, Signal, Slot, QTimer, QEvent
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
)

from lucid.epics.widgets.label import PVLabel
from lucid.epics.widgets.lineedit import PVLineEdit
from lucid.epics.widgets.status_indicator import StatusIndicator
from lucid.epics.widgets.style import (
    get_success_color,
    get_error_color,
    get_warning_color,
)


class PVMotor(QWidget):
    """
    A comprehensive motor control widget for EPICS motor records.

    The widget provides a simple, focused interface with:
    - Current position readback (RBV)
    - Setpoint entry (VAL)
    - Tweak buttons for relative motion (TWF/TWR)
    - Status indicators for moving, limits, and direction
    - Stop button
    - Units display

    An expandable "Advanced" section provides:
    - Velocity control
    - Acceleration settings
    - Soft limit display/control
    - Additional status information

    Attributes:
        prefix: The motor record PV prefix (e.g., "IOC:m1").
        connected: Whether all essential PVs are connected.

    Signals:
        connection_changed: Emitted when overall connection state changes.
        motion_started: Emitted when motor starts moving.
        motion_finished: Emitted when motor stops moving.
        limit_hit: Emitted when a limit switch is triggered.

    Example:
        >>> motor = PVMotor("IOC:m1")
        >>> motor.show()
    """

    widget_type: ClassVar[str] = "PVMotor"
    widget_description: ClassVar[str] = "Motor record control with position, status, and tweak controls"

    connection_changed = Signal(bool)
    motion_started = Signal()
    motion_finished = Signal()
    limit_hit = Signal(str)  # "high" or "low"

    def __init__(
        self,
        prefix: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """
        Initialize the motor widget.

        Args:
            prefix: The EPICS motor record prefix (e.g., "IOC:m1").
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        self._prefix = prefix
        self._pvs: dict[str, Any] = {}
        self._values: dict[str, Any] = {}
        self._connected_pvs: set[str] = set()
        self._was_moving = False

        self._setup_ui()
        self.setToolTip(prefix)

        # Defer PV connection to allow GUI to show first
        if prefix:
            QTimer.singleShot(0, self._connect_pvs)

    @Property(str)
    def prefix(self) -> str:
        """The motor record PV prefix."""
        return self._prefix

    @prefix.setter
    def prefix(self, value: str) -> None:
        if value != self._prefix:
            self._disconnect_pvs()
            self._prefix = value
            self.setToolTip(value)
            if value:
                self._connect_pvs()

    def _setup_ui(self) -> None:
        """Create the widget UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # === Top Status Bar ===
        status_bar = QHBoxLayout()
        status_bar.setSpacing(12)

        # Connection indicator
        self._conn_indicator = StatusIndicator(size=16)
        self._conn_label = QLabel("Disconnected")
        status_bar.addWidget(self._conn_indicator)
        status_bar.addWidget(self._conn_label)
        status_bar.addStretch()

        # Direction indicator
        self._dir_label = QLabel("\u2b24")  # Will show arrow
        self._dir_label.setToolTip("Direction of travel")
        status_bar.addWidget(self._dir_label)

        # Moving indicator
        self._moving_indicator = StatusIndicator(size=16)
        self._moving_label = QLabel("Idle")
        status_bar.addWidget(self._moving_indicator)
        status_bar.addWidget(self._moving_label)

        main_layout.addLayout(status_bar)

        # === Position Display ===
        pos_group = QGroupBox("Position")
        pos_layout = QGridLayout(pos_group)
        pos_layout.setSpacing(8)

        # Readback (RBV) - prominent display
        pos_layout.addWidget(QLabel("Readback:"), 0, 0)
        self._rbv_display = PVLabel(show_units=True)
        self._rbv_display.setStyleSheet("""
            QLabel {
                font-size: 18pt;
                font-weight: bold;
                font-family: monospace;
                padding: 4px 8px;
            }
        """)
        pos_layout.addWidget(self._rbv_display, 0, 1, 1, 2)

        # Setpoint (VAL) - editable
        pos_layout.addWidget(QLabel("Setpoint:"), 1, 0)
        self._setpoint_edit = PVLineEdit(
            show_units=False, write_on_enter=True,
        )
        self._setpoint_edit._line_edit.setPlaceholderText("Enter position")
        self._setpoint_edit._line_edit.returnPressed.connect(self._on_setpoint_enter)
        pos_layout.addWidget(self._setpoint_edit, 1, 1)

        # Go button
        self._go_btn = QPushButton("Go")
        self._go_btn.setFixedWidth(50)
        self._go_btn.clicked.connect(self._on_go_clicked)
        pos_layout.addWidget(self._go_btn, 1, 2)

        main_layout.addWidget(pos_group)

        # === Tweak Controls (Relative Motion) ===
        tweak_group = QGroupBox("Relative Motion")
        tweak_layout = QHBoxLayout(tweak_group)
        tweak_layout.setSpacing(8)

        # Tweak reverse button
        self._twr_btn = QPushButton("\u25c0")
        self._twr_btn.setToolTip("Tweak Reverse (TWR)")
        self._twr_btn.setFixedWidth(40)
        self._twr_btn.clicked.connect(self._on_tweak_reverse)
        tweak_layout.addWidget(self._twr_btn)

        # Tweak step size
        self._tweak_edit = PVLineEdit(
            show_units=False, write_on_enter=True, write_on_focus_out=True,
        )
        self._tweak_edit.setToolTip("Tweak step size (TWV)")
        self._tweak_edit.setMaximumWidth(80)
        tweak_layout.addWidget(self._tweak_edit)

        # Tweak forward button
        self._twf_btn = QPushButton("\u25b6")
        self._twf_btn.setToolTip("Tweak Forward (TWF)")
        self._twf_btn.setFixedWidth(40)
        self._twf_btn.clicked.connect(self._on_tweak_forward)
        tweak_layout.addWidget(self._twf_btn)

        tweak_layout.addStretch()

        main_layout.addWidget(tweak_group)

        # === Limit Indicators ===
        limit_layout = QHBoxLayout()
        limit_layout.setSpacing(16)

        # Low limit
        self._lls_indicator = StatusIndicator(size=16)
        lls_label = QLabel("Low Limit")
        limit_layout.addWidget(self._lls_indicator)
        limit_layout.addWidget(lls_label)

        limit_layout.addStretch()

        # High limit
        self._hls_indicator = StatusIndicator(size=16)
        hls_label = QLabel("High Limit")
        limit_layout.addWidget(self._hls_indicator)
        limit_layout.addWidget(hls_label)

        main_layout.addLayout(limit_layout)

        # === Control Buttons ===
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        # Stop button (prominent)
        self._stop_btn = QPushButton("STOP")
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {get_error_color()};
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #ff0000;
            }}
            QPushButton:pressed {{
                background-color: #990000;
            }}
        """)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        btn_layout.addWidget(self._stop_btn)

        btn_layout.addStretch()

        # Enable checkbox (using button for clarity)
        self._enable_btn = QPushButton("Enabled")
        self._enable_btn.setCheckable(True)
        self._enable_btn.setChecked(True)
        self._enable_btn.clicked.connect(self._on_enable_toggled)
        btn_layout.addWidget(self._enable_btn)

        main_layout.addLayout(btn_layout)

        # === Advanced Section (Progressive Disclosure) ===
        self._advanced_btn = QPushButton("\u25b6 Advanced")
        self._advanced_btn.setFlat(True)
        self._advanced_btn.setCheckable(True)
        self._advanced_btn.clicked.connect(self._toggle_advanced)
        main_layout.addWidget(self._advanced_btn)

        self._advanced_group = QGroupBox()
        self._advanced_group.setVisible(False)
        advanced_layout = QGridLayout(self._advanced_group)
        advanced_layout.setSpacing(8)

        # Velocity
        advanced_layout.addWidget(QLabel("Velocity:"), 0, 0)
        self._velo_edit = PVLineEdit(
            show_units=False, write_on_enter=True, write_on_focus_out=True,
        )
        advanced_layout.addWidget(self._velo_edit, 0, 1)
        self._velo_units = QLabel("units/s")
        advanced_layout.addWidget(self._velo_units, 0, 2)

        # Acceleration
        advanced_layout.addWidget(QLabel("Accel Time:"), 1, 0)
        self._accl_edit = PVLineEdit(
            show_units=False, write_on_enter=True, write_on_focus_out=True,
        )
        advanced_layout.addWidget(self._accl_edit, 1, 1)
        advanced_layout.addWidget(QLabel("sec"), 1, 2)

        # Soft limits (display only in basic mode)
        advanced_layout.addWidget(QLabel("Low Limit:"), 2, 0)
        self._llm_display = QLabel("---")
        advanced_layout.addWidget(self._llm_display, 2, 1)

        advanced_layout.addWidget(QLabel("High Limit:"), 3, 0)
        self._hlm_display = QLabel("---")
        advanced_layout.addWidget(self._hlm_display, 3, 1)

        # Motor status (MSTA) breakdown
        advanced_layout.addWidget(QLabel("Status:"), 4, 0)
        self._msta_display = QLabel("---")
        self._msta_display.setWordWrap(True)
        advanced_layout.addWidget(self._msta_display, 4, 1, 1, 2)

        main_layout.addWidget(self._advanced_group)

        main_layout.addStretch()

        # Initial state
        self._update_connection_display(False)
        self._set_controls_enabled(False)

    def _toggle_advanced(self) -> None:
        """Toggle the advanced section visibility."""
        visible = self._advanced_btn.isChecked()
        self._advanced_group.setVisible(visible)
        self._advanced_btn.setText("\u25bc Advanced" if visible else "\u25b6 Advanced")

    def _connect_pvs(self) -> None:
        """Connect to all motor record PVs."""
        if not self._prefix:
            return

        from lucid.epics.ca.pv import PV

        # PV widgets handle their own connections
        self._rbv_display.pv_name = f"{self._prefix}.RBV"
        self._setpoint_edit.pv_name = f"{self._prefix}.VAL"
        self._tweak_edit.pv_name = f"{self._prefix}.TWV"
        self._velo_edit.pv_name = f"{self._prefix}.VELO"
        self._accl_edit.pv_name = f"{self._prefix}.ACCL"

        # Listen for widget connection changes to update overall status
        self._rbv_display.connection_changed.connect(self._on_widget_connection_changed)
        self._setpoint_edit.connection_changed.connect(self._on_widget_connection_changed)

        # Manually managed PVs for status, limits, and control
        pv_fields = {
            "TWF": "tweak_fwd",     # Tweak forward
            "TWR": "tweak_rev",     # Tweak reverse
            "MOVN": "moving",       # Motor is moving
            "DMOV": "done_moving",  # Done moving
            "HLS": "high_limit",    # At high limit switch
            "LLS": "low_limit",     # At low limit switch
            "TDIR": "direction",    # Direction of travel
            "STOP": "stop",         # Stop motor
            "SPMG": "spmg",         # Stop/Pause/Move/Go
            # Advanced
            "HLM": "high_lim_val",  # High soft limit
            "LLM": "low_lim_val",   # Low soft limit
            "MSTA": "msta",         # Motor status word
        }

        for field, name in pv_fields.items():
            pv_name = f"{self._prefix}.{field}"
            pv = PV(pv_name, parent=self)
            pv.value_changed.connect(lambda v, n=name: self._on_pv_value(n, v))
            pv.connection_changed.connect(lambda c, n=name: self._on_pv_connection(n, c))
            pv.connect_pv()
            self._pvs[name] = pv

    def _disconnect_pvs(self) -> None:
        """Disconnect all PVs."""
        # Disconnect widget connection signals
        try:
            self._rbv_display.connection_changed.disconnect(self._on_widget_connection_changed)
            self._setpoint_edit.connection_changed.disconnect(self._on_widget_connection_changed)
        except RuntimeError:
            pass  # Already disconnected

        # Disconnect PV widgets
        self._rbv_display.pv_name = ""
        self._setpoint_edit.pv_name = ""
        self._tweak_edit.pv_name = ""
        self._velo_edit.pv_name = ""
        self._accl_edit.pv_name = ""

        # Disconnect manually managed PVs
        for pv in self._pvs.values():
            pv.disconnect_pv()
            pv.deleteLater()
        self._pvs.clear()
        self._connected_pvs.clear()
        self._values.clear()
        self._update_connection_display(False)

    @Slot(str, object)
    def _on_pv_value(self, name: str, value: Any) -> None:
        """Handle PV value updates for manually managed PVs."""
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]

        self._values[name] = value

        if name == "moving":
            self._update_moving_status()
        elif name == "done_moving":
            self._update_done_moving()
        elif name == "high_limit":
            self._update_limit_indicators()
        elif name == "low_limit":
            self._update_limit_indicators()
        elif name == "direction":
            self._update_direction()
        elif name == "high_lim_val":
            self._hlm_display.setText(f"{value:.3f}")
        elif name == "low_lim_val":
            self._llm_display.setText(f"{value:.3f}")
        elif name == "msta":
            self._update_msta_display()
        elif name == "spmg":
            self._update_enable_button()

    @Slot(bool)
    def _on_widget_connection_changed(self, _connected: bool) -> None:
        """Handle connection changes from PV widgets."""
        self._check_overall_connection()

    @Slot(str, bool)
    def _on_pv_connection(self, name: str, connected: bool) -> None:
        """Handle PV connection state changes for manually managed PVs."""
        if connected:
            self._connected_pvs.add(name)
        else:
            self._connected_pvs.discard(name)

        self._check_overall_connection()

    def _check_overall_connection(self) -> None:
        """Check that essential PVs are connected (both widget-managed and manual)."""
        widgets_connected = (
            self._rbv_display.connected
            and self._setpoint_edit.connected
        )
        manual_connected = "moving" in self._connected_pvs
        is_connected = widgets_connected and manual_connected
        self._update_connection_display(is_connected)
        self._set_controls_enabled(is_connected)

    def _update_connection_display(self, connected: bool) -> None:
        """Update connection status display."""
        if connected:
            self._conn_indicator.set_state("on")
            self._conn_label.setText("Connected")
        else:
            self._conn_indicator.set_state("off")
            self._conn_label.setText("Disconnected")
        self.connection_changed.emit(connected)

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable control widgets."""
        # PV widgets use readonly property
        self._setpoint_edit.readonly = not enabled
        self._tweak_edit.readonly = not enabled
        self._velo_edit.readonly = not enabled
        self._accl_edit.readonly = not enabled

        # Regular Qt widgets use setEnabled
        self._go_btn.setEnabled(enabled)
        self._twf_btn.setEnabled(enabled)
        self._twr_btn.setEnabled(enabled)
        self._stop_btn.setEnabled(enabled)
        self._enable_btn.setEnabled(enabled)

    def _update_moving_status(self) -> None:
        moving = bool(self._values.get("moving", 0))
        if moving:
            self._moving_indicator.set_state("on")
            self._moving_label.setText("Moving")
            if not self._was_moving:
                self.motion_started.emit()
        else:
            self._moving_indicator.set_state("off")
            self._moving_label.setText("Idle")
            if self._was_moving:
                self.motion_finished.emit()
        self._was_moving = moving

    def _update_done_moving(self) -> None:
        dmov = bool(self._values.get("done_moving", 1))
        if dmov and self._was_moving:
            self._moving_indicator.set_state("off")
            self._moving_label.setText("Idle")
            self._was_moving = False
            self.motion_finished.emit()

    def _update_limit_indicators(self) -> None:
        hls = bool(self._values.get("high_limit", 0))
        lls = bool(self._values.get("low_limit", 0))
        self._hls_indicator.set_state("error" if hls else "off")
        self._lls_indicator.set_state("error" if lls else "off")
        if hls:
            self.limit_hit.emit("high")
        if lls:
            self.limit_hit.emit("low")

    def _update_direction(self) -> None:
        tdir = self._values.get("direction", 0)
        if tdir:
            self._dir_label.setText("\u25b2")
            self._dir_label.setStyleSheet(f"color: {get_success_color()};")
        else:
            self._dir_label.setText("\u25bc")
            self._dir_label.setStyleSheet(f"color: {get_warning_color()};")

    def _update_msta_display(self) -> None:
        msta = self._values.get("msta", 0)
        if isinstance(msta, (list, tuple)):
            msta = msta[0] if msta else 0
        status_bits = []
        if msta & 0x0001:
            status_bits.append("Direction")
        if msta & 0x0002:
            status_bits.append("Done")
        if msta & 0x0004:
            status_bits.append("+Limit")
        if msta & 0x0008:
            status_bits.append("Home")
        if msta & 0x0020:
            status_bits.append("Closed-loop")
        if msta & 0x0040:
            status_bits.append("Slip/Stall")
        if msta & 0x0080:
            status_bits.append("Home switch")
        if msta & 0x0100:
            status_bits.append("Encoder")
        if msta & 0x0200:
            status_bits.append("Problem")
        if msta & 0x0400:
            status_bits.append("Moving")
        if msta & 0x0800:
            status_bits.append("Gain support")
        if msta & 0x1000:
            status_bits.append("Comm error")
        if msta & 0x2000:
            status_bits.append("-Limit")
        if msta & 0x4000:
            status_bits.append("Homed")
        self._msta_display.setText(", ".join(status_bits) if status_bits else "OK")

    def _update_enable_button(self) -> None:
        spmg = self._values.get("spmg", 3)
        enabled = spmg >= 2
        self._enable_btn.blockSignals(True)
        self._enable_btn.setChecked(enabled)
        self._enable_btn.setText("Enabled" if enabled else "Disabled")
        self._enable_btn.blockSignals(False)

    # === User Actions ===

    def _on_setpoint_enter(self) -> None:
        self._on_go_clicked()

    def _on_go_clicked(self) -> None:
        try:
            value = float(self._setpoint_edit._line_edit.text())
            self._setpoint_edit.write_value(value)
        except (ValueError, RuntimeError):
            pass

    def _on_tweak_forward(self) -> None:
        if "tweak_fwd" in self._pvs:
            self._pvs["tweak_fwd"].put(1)
        self._do_relative_move(1)

    def _on_tweak_reverse(self) -> None:
        if "tweak_rev" in self._pvs:
            self._pvs["tweak_rev"].put(1)
        self._do_relative_move(-1)

    def _do_relative_move(self, direction: int) -> None:
        try:
            tweak_val = float(self._tweak_edit._line_edit.text())
        except ValueError:
            tweak_val = 1.0
        current = self._rbv_display._value
        if current is None:
            current = 0.0
        new_pos = current + (direction * tweak_val)
        try:
            self._setpoint_edit.write_value(new_pos)
        except RuntimeError:
            pass

    def _on_stop_clicked(self) -> None:
        if "stop" in self._pvs:
            self._pvs["stop"].put(1)

    def _on_enable_toggled(self, checked: bool) -> None:
        if "spmg" in self._pvs:
            self._pvs["spmg"].put(3 if checked else 0)

    # === Public API ===

    def move_to(self, position: float) -> None:
        try:
            self._setpoint_edit.write_value(position)
        except RuntimeError:
            pass

    def move_relative(self, distance: float) -> None:
        current = self._rbv_display._value
        if current is None:
            current = 0.0
        self.move_to(current + distance)

    def stop(self) -> None:
        self._on_stop_clicked()

    def tweak_forward(self) -> None:
        self._on_tweak_forward()

    def tweak_reverse(self) -> None:
        self._on_tweak_reverse()

    @property
    def position(self) -> float | None:
        return self._rbv_display._value

    @property
    def setpoint(self) -> float | None:
        return self._setpoint_edit._value

    @property
    def is_moving(self) -> bool:
        return bool(self._values.get("moving", 0))

    @property
    def at_high_limit(self) -> bool:
        return bool(self._values.get("high_limit", 0))

    @property
    def at_low_limit(self) -> bool:
        return bool(self._values.get("low_limit", 0))

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "widget_type": self.widget_type,
            "widget_description": self.widget_description,
            "prefix": self._prefix,
            "connected_pvs": list(self._connected_pvs),
            "position": self.position,
            "setpoint": self.setpoint,
            "is_moving": self.is_moving,
            "at_high_limit": self.at_high_limit,
            "at_low_limit": self.at_low_limit,
            "units": self._rbv_display._units,
            "values": dict(self._values),
        }

    def closeEvent(self, event) -> None:
        self._disconnect_pvs()
        super().closeEvent(event)

    # -- Tooltip forwarding ---------------------------------------------------
    # Forward ToolTip events from child widgets (which have no tooltip of
    # their own) so hovering any part of the motor widget shows the motor
    # prefix. Child widgets with their own tooltip (e.g. "Tweak Forward")
    # take precedence.

    def childEvent(self, event) -> None:
        super().childEvent(event)
        if event.type() == QEvent.Type.ChildAdded:
            child = event.child()
            if isinstance(child, QWidget):
                child.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if (
            event.type() == QEvent.Type.ToolTip
            and isinstance(obj, QWidget)
            and obj is not self
            and not obj.toolTip()
        ):
            tip = self.toolTip()
            if tip:
                from PySide6.QtWidgets import QToolTip

                try:
                    pos = event.globalPos()
                except AttributeError:
                    pos = event.globalPosition().toPoint()
                QToolTip.showText(pos, tip, obj)
                return True
        return super().eventFilter(obj, event)
