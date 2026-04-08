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

from PySide6.QtCore import Property, Signal, Slot, Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QFrame,
    QSizePolicy,
)
from PySide6.QtGui import QDoubleValidator

from lucid.epics.widgets.style import (
    is_dark_theme,
    get_success_color,
    get_error_color,
    get_warning_color,
    get_disconnected_color,
)


class StatusIndicator(QFrame):
    """A small circular status indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._state = "off"
        self._update_style()

    def set_state(self, state: str) -> None:
        """Set indicator state: 'off', 'on', 'warning', 'error'."""
        self._state = state
        self._update_style()

    def _update_style(self) -> None:
        colors = {
            "off": "#666666",
            "on": get_success_color(),
            "warning": get_warning_color(),
            "error": get_error_color(),
        }
        color = colors.get(self._state, colors["off"])
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: 8px;
                border: 1px solid #333;
            }}
        """)


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
        self._units = ""
        self._precision = 3
        self._was_moving = False

        self._setup_ui()

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
        self._conn_indicator = StatusIndicator()
        self._conn_label = QLabel("Disconnected")
        status_bar.addWidget(self._conn_indicator)
        status_bar.addWidget(self._conn_label)
        status_bar.addStretch()

        # Direction indicator
        self._dir_label = QLabel("\u2b24")  # Will show arrow
        self._dir_label.setToolTip("Direction of travel")
        status_bar.addWidget(self._dir_label)

        # Moving indicator
        self._moving_indicator = StatusIndicator()
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
        self._rbv_display = QLabel("---")
        self._rbv_display.setStyleSheet("""
            QLabel {
                font-size: 18pt;
                font-weight: bold;
                font-family: monospace;
                padding: 4px 8px;
            }
        """)
        pos_layout.addWidget(self._rbv_display, 0, 1)

        # Units
        self._units_label = QLabel("")
        pos_layout.addWidget(self._units_label, 0, 2)

        # Setpoint (VAL) - editable
        pos_layout.addWidget(QLabel("Setpoint:"), 1, 0)
        self._setpoint_edit = QLineEdit()
        self._setpoint_edit.setPlaceholderText("Enter position")
        self._setpoint_edit.setValidator(QDoubleValidator())
        self._setpoint_edit.returnPressed.connect(self._on_setpoint_enter)
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
        self._tweak_edit = QLineEdit("1.0")
        self._tweak_edit.setValidator(QDoubleValidator(0.0001, 1000000, 6))
        self._tweak_edit.setToolTip("Tweak step size (TWV)")
        self._tweak_edit.setMaximumWidth(80)
        self._tweak_edit.editingFinished.connect(self._on_tweak_value_changed)
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
        self._lls_indicator = StatusIndicator()
        lls_label = QLabel("Low Limit")
        limit_layout.addWidget(self._lls_indicator)
        limit_layout.addWidget(lls_label)

        limit_layout.addStretch()

        # High limit
        self._hls_indicator = StatusIndicator()
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
        self._velo_edit = QLineEdit()
        self._velo_edit.setValidator(QDoubleValidator(0, 1000000, 3))
        self._velo_edit.editingFinished.connect(self._on_velo_changed)
        advanced_layout.addWidget(self._velo_edit, 0, 1)
        self._velo_units = QLabel("units/s")
        advanced_layout.addWidget(self._velo_units, 0, 2)

        # Acceleration
        advanced_layout.addWidget(QLabel("Accel Time:"), 1, 0)
        self._accl_edit = QLineEdit()
        self._accl_edit.setValidator(QDoubleValidator(0, 1000, 3))
        self._accl_edit.editingFinished.connect(self._on_accl_changed)
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

        # Essential PVs for basic operation
        pv_fields = {
            "VAL": "setpoint",      # Desired position
            "RBV": "readback",      # Current position
            "TWV": "tweak_val",     # Tweak step size
            "TWF": "tweak_fwd",     # Tweak forward
            "TWR": "tweak_rev",     # Tweak reverse
            "MOVN": "moving",       # Motor is moving
            "DMOV": "done_moving",  # Done moving
            "HLS": "high_limit",    # At high limit switch
            "LLS": "low_limit",     # At low limit switch
            "TDIR": "direction",    # Direction of travel
            "STOP": "stop",         # Stop motor
            "SPMG": "spmg",         # Stop/Pause/Move/Go
            "EGU": "units",         # Engineering units
            "PREC": "precision",    # Display precision
            # Advanced
            "VELO": "velocity",     # Velocity
            "ACCL": "acceleration", # Acceleration time
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
        for pv in self._pvs.values():
            pv.disconnect_pv()
            pv.deleteLater()
        self._pvs.clear()
        self._connected_pvs.clear()
        self._values.clear()
        self._update_connection_display(False)

    @Slot(str, object)
    def _on_pv_value(self, name: str, value: Any) -> None:
        """Handle PV value updates."""
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]

        self._values[name] = value

        if name == "readback":
            self._update_readback()
        elif name == "setpoint":
            self._update_setpoint_display()
        elif name == "tweak_val":
            self._update_tweak_display()
        elif name == "moving":
            self._update_moving_status()
        elif name == "done_moving":
            self._update_done_moving()
        elif name == "high_limit":
            self._update_limit_indicators()
        elif name == "low_limit":
            self._update_limit_indicators()
        elif name == "direction":
            self._update_direction()
        elif name == "units":
            self._update_units()
        elif name == "precision":
            self._precision = int(value) if value else 3
            self._update_readback()
        elif name == "velocity":
            self._update_velocity_display()
        elif name == "acceleration":
            self._update_accel_display()
        elif name == "high_lim_val":
            self._hlm_display.setText(f"{value:.{self._precision}f}")
        elif name == "low_lim_val":
            self._llm_display.setText(f"{value:.{self._precision}f}")
        elif name == "msta":
            self._update_msta_display()
        elif name == "spmg":
            self._update_enable_button()

    @Slot(str, bool)
    def _on_pv_connection(self, name: str, connected: bool) -> None:
        """Handle PV connection state changes."""
        if connected:
            self._connected_pvs.add(name)
        else:
            self._connected_pvs.discard(name)

        essential = {"readback", "setpoint", "moving"}
        is_connected = essential.issubset(self._connected_pvs)
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
        self._setpoint_edit.setEnabled(enabled)
        self._go_btn.setEnabled(enabled)
        self._twf_btn.setEnabled(enabled)
        self._twr_btn.setEnabled(enabled)
        self._tweak_edit.setEnabled(enabled)
        self._stop_btn.setEnabled(enabled)
        self._enable_btn.setEnabled(enabled)
        self._velo_edit.setEnabled(enabled)
        self._accl_edit.setEnabled(enabled)

    def _update_readback(self) -> None:
        value = self._values.get("readback")
        if value is not None:
            text = f"{value:.{self._precision}f}"
            self._rbv_display.setText(text)
        else:
            self._rbv_display.setText("---")

    def _update_setpoint_display(self) -> None:
        if not self._setpoint_edit.hasFocus():
            value = self._values.get("setpoint")
            if value is not None:
                self._setpoint_edit.setText(f"{value:.{self._precision}f}")

    def _update_tweak_display(self) -> None:
        if not self._tweak_edit.hasFocus():
            value = self._values.get("tweak_val")
            if value is not None:
                self._tweak_edit.setText(f"{value:.{self._precision}f}")

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

    def _update_units(self) -> None:
        units = self._values.get("units", "")
        if isinstance(units, bytes):
            units = units.decode("utf-8", errors="replace")
        self._units = str(units)
        self._units_label.setText(self._units)
        self._velo_units.setText(f"{self._units}/s")

    def _update_velocity_display(self) -> None:
        if not self._velo_edit.hasFocus():
            value = self._values.get("velocity")
            if value is not None:
                self._velo_edit.setText(f"{value:.3f}")

    def _update_accel_display(self) -> None:
        if not self._accl_edit.hasFocus():
            value = self._values.get("acceleration")
            if value is not None:
                self._accl_edit.setText(f"{value:.3f}")

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
            value = float(self._setpoint_edit.text())
            if "setpoint" in self._pvs:
                self._pvs["setpoint"].put(value)
        except ValueError:
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
            tweak_val = float(self._tweak_edit.text())
        except ValueError:
            tweak_val = self._values.get("tweak_val", 1.0)
        current = self._values.get("readback", 0.0)
        if current is None:
            current = 0.0
        new_pos = current + (direction * tweak_val)
        if "setpoint" in self._pvs:
            self._pvs["setpoint"].put(new_pos)

    def _on_tweak_value_changed(self) -> None:
        try:
            value = float(self._tweak_edit.text())
            if "tweak_val" in self._pvs:
                self._pvs["tweak_val"].put(value)
        except ValueError:
            pass

    def _on_stop_clicked(self) -> None:
        if "stop" in self._pvs:
            self._pvs["stop"].put(1)

    def _on_enable_toggled(self, checked: bool) -> None:
        if "spmg" in self._pvs:
            self._pvs["spmg"].put(3 if checked else 0)

    def _on_velo_changed(self) -> None:
        try:
            value = float(self._velo_edit.text())
            if "velocity" in self._pvs:
                self._pvs["velocity"].put(value)
        except ValueError:
            pass

    def _on_accl_changed(self) -> None:
        try:
            value = float(self._accl_edit.text())
            if "acceleration" in self._pvs:
                self._pvs["acceleration"].put(value)
        except ValueError:
            pass

    # === Public API ===

    def move_to(self, position: float) -> None:
        if "setpoint" in self._pvs:
            self._pvs["setpoint"].put(position)

    def move_relative(self, distance: float) -> None:
        current = self._values.get("readback", 0)
        self.move_to(current + distance)

    def stop(self) -> None:
        self._on_stop_clicked()

    def tweak_forward(self) -> None:
        self._on_tweak_forward()

    def tweak_reverse(self) -> None:
        self._on_tweak_reverse()

    @property
    def position(self) -> float | None:
        return self._values.get("readback")

    @property
    def setpoint(self) -> float | None:
        return self._values.get("setpoint")

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
            "units": self._units,
            "precision": self._precision,
            "values": dict(self._values),
        }

    def closeEvent(self, event) -> None:
        self._disconnect_pvs()
        super().closeEvent(event)
