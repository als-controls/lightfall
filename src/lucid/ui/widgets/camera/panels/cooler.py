"""Cooler control panel for Andor-style cameras.

Provides controls for:
- Cooler on/off
- Temperature setpoint
- Actual temperature readback
- Cooler status display
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QWidget,
)

from lucid.utils.logging import logger


# Andor cooler status values
COOLER_STATUS = {
    0: "Off",
    1: "Stabilized",
    2: "Cooling",
    3: "Drift",
    4: "Not Stabilized",
    5: "Fault",
    6: "Sensor Over Temp",
}


class StatusIndicator(QFrame):
    """Small circular status indicator."""

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
            QFrame {{
                background-color: {color};
                border-radius: 6px;
                border: 1px solid #333;
            }}
        """)


class CoolerPanel(QGroupBox):
    """Cooler control panel for Andor-style cameras.

    Provides controls for:
    - Cooler on/off toggle
    - Temperature setpoint
    - Actual temperature readback
    - Cooler status display

    Standard Andor PVs used:
    - AndorCooler: Cooler enable (0=Off, 1=On)
    - AndorTempSetPoint: Temperature setpoint (°C)
    - Temperature_RBV: Actual temperature (°C)
    - TemperatureStatus_RBV: Cooler status

    Signals:
        cooler_state_changed: Emitted when cooler on/off changes.
        setpoint_changed: Emitted when setpoint is changed.
    """

    cooler_state_changed = Signal(bool)  # True = On
    setpoint_changed = Signal(float)

    def __init__(
        self,
        prefix: str = "",
        cam_suffix: str = "cam1:",
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the cooler panel.

        Args:
            prefix: EPICS PV prefix (e.g., "13ANDOR1:").
            cam_suffix: Camera plugin suffix (default "cam1:").
            parent: Parent widget.
        """
        super().__init__("Cooler", parent)
        self._prefix = prefix
        self._cam_suffix = cam_suffix
        self._cam_prefix = f"{prefix}{cam_suffix}" if prefix else ""

        # PV state
        self._pvs: dict[str, Any] = {}
        self._values: dict[str, Any] = {}
        self._connected_pvs: set[str] = set()

        self._setup_ui()

        if prefix:
            QTimer.singleShot(0, self._connect_pvs)

    def set_prefix(self, prefix: str) -> None:
        """Set the PV prefix and reconnect.

        Args:
            prefix: EPICS PV prefix.
        """
        if prefix != self._prefix:
            self._disconnect_pvs()
            self._prefix = prefix
            self._cam_prefix = f"{prefix}{self._cam_suffix}" if prefix else ""
            if prefix:
                self._connect_pvs()

    def _setup_ui(self) -> None:
        """Create the panel UI."""
        layout = QGridLayout(self)
        layout.setSpacing(8)

        row = 0

        # Cooler state toggle
        layout.addWidget(QLabel("State:"), row, 0)
        self._cooler_combo = QComboBox()
        self._cooler_combo.addItems(["Off", "On"])
        self._cooler_combo.currentIndexChanged.connect(self._on_cooler_state_changed)
        layout.addWidget(self._cooler_combo, row, 1)

        row += 1

        # Temperature setpoint
        layout.addWidget(QLabel("Setpoint:"), row, 0)
        self._setpoint_spin = QDoubleSpinBox()
        self._setpoint_spin.setRange(-100.0, 50.0)
        self._setpoint_spin.setDecimals(1)
        self._setpoint_spin.setSuffix(" °C")
        self._setpoint_spin.editingFinished.connect(self._on_setpoint_changed)
        layout.addWidget(self._setpoint_spin, row, 1)

        row += 1

        # Actual temperature
        layout.addWidget(QLabel("Actual:"), row, 0)
        self._temp_label = QLabel("--- °C")
        self._temp_label.setStyleSheet("font-family: monospace; font-weight: bold;")
        layout.addWidget(self._temp_label, row, 1)

        row += 1

        # Cooler status with indicator
        layout.addWidget(QLabel("Status:"), row, 0)
        status_widget = QWidget()
        status_layout = QGridLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)

        self._status_indicator = StatusIndicator()
        status_layout.addWidget(self._status_indicator, 0, 0)

        self._status_label = QLabel("---")
        status_layout.addWidget(self._status_label, 0, 1)

        layout.addWidget(status_widget, row, 1)

        # Initial state - disabled
        self._set_controls_enabled(False)

    def _connect_pvs(self) -> None:
        """Connect to Andor cooler PVs."""
        if not self._cam_prefix:
            return

        try:
            from epics_pyside.ca.pv import PV

            # PVs to connect
            pv_fields = {
                "AndorCooler": "cooler",
                "AndorCooler_RBV": "cooler_rbv",
                "AndorTempSetPoint": "setpoint",
                "AndorTempSetPoint_RBV": "setpoint_rbv",
                "Temperature_RBV": "temperature",
                "TemperatureStatus_RBV": "temp_status",
            }

            for field, name in pv_fields.items():
                pv_name = f"{self._cam_prefix}{field}"
                pv = PV(pv_name, parent=self)
                pv.value_changed.connect(lambda v, n=name: self._on_pv_value(n, v))
                pv.connection_changed.connect(lambda c, n=name: self._on_pv_connection(n, c))
                pv.connect_pv()
                self._pvs[name] = pv

        except ImportError:
            logger.warning("epics_pyside not available, cooler panel disabled")

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
        if name == "cooler_rbv":
            self._update_cooler_display()
        elif name == "setpoint_rbv":
            self._update_setpoint_display()
        elif name == "temperature":
            self._update_temperature_display()
        elif name == "temp_status":
            self._update_status_display()

    @Slot(str, bool)
    def _on_pv_connection(self, name: str, connected: bool) -> None:
        """Handle PV connection state changes."""
        if connected:
            self._connected_pvs.add(name)
        else:
            self._connected_pvs.discard(name)

        # Consider connected if we have temperature readback
        is_connected = "temperature" in self._connected_pvs
        self._set_controls_enabled(is_connected)

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable controls."""
        self._cooler_combo.setEnabled(enabled)
        self._setpoint_spin.setEnabled(enabled)

    def _update_cooler_display(self) -> None:
        """Update cooler state display."""
        value = self._values.get("cooler_rbv")
        if value is not None:
            idx = int(value)
            self._cooler_combo.blockSignals(True)
            self._cooler_combo.setCurrentIndex(min(idx, 1))
            self._cooler_combo.blockSignals(False)

    def _update_setpoint_display(self) -> None:
        """Update setpoint display."""
        if not self._setpoint_spin.hasFocus():
            value = self._values.get("setpoint_rbv")
            if value is not None:
                self._setpoint_spin.blockSignals(True)
                self._setpoint_spin.setValue(float(value))
                self._setpoint_spin.blockSignals(False)

    def _update_temperature_display(self) -> None:
        """Update actual temperature display."""
        value = self._values.get("temperature")
        if value is not None:
            self._temp_label.setText(f"{float(value):.1f} °C")

    def _update_status_display(self) -> None:
        """Update cooler status display."""
        value = self._values.get("temp_status")
        if value is not None:
            status_int = int(value)
            status_name = COOLER_STATUS.get(status_int, f"Unknown ({status_int})")
            self._status_label.setText(status_name)

            # Update indicator color
            if status_int == 0:  # Off
                self._status_indicator.set_state("off")
            elif status_int == 1:  # Stabilized
                self._status_indicator.set_state("on")
            elif status_int in (2, 3, 4):  # Cooling, Drift, Not Stabilized
                self._status_indicator.set_state("warning")
            else:  # Fault, Over Temp
                self._status_indicator.set_state("error")

    def _on_cooler_state_changed(self, index: int) -> None:
        """Handle cooler state change."""
        if "cooler" in self._pvs:
            self._pvs["cooler"].put(index)
            self.cooler_state_changed.emit(bool(index))

    def _on_setpoint_changed(self) -> None:
        """Handle setpoint change."""
        value = self._setpoint_spin.value()
        if "setpoint" in self._pvs:
            self._pvs["setpoint"].put(value)
            self.setpoint_changed.emit(value)

    # === Public API ===

    def set_cooler_on(self, on: bool) -> None:
        """Turn cooler on or off.

        Args:
            on: True to turn on, False to turn off.
        """
        if "cooler" in self._pvs:
            self._pvs["cooler"].put(1 if on else 0)

    def set_temperature_setpoint(self, temp: float) -> None:
        """Set the temperature setpoint.

        Args:
            temp: Temperature in °C.
        """
        if "setpoint" in self._pvs:
            self._pvs["setpoint"].put(temp)

    @property
    def temperature(self) -> float | None:
        """Current actual temperature in °C."""
        val = self._values.get("temperature")
        return float(val) if val is not None else None

    @property
    def setpoint(self) -> float | None:
        """Current temperature setpoint in °C."""
        val = self._values.get("setpoint_rbv")
        return float(val) if val is not None else None

    @property
    def is_cooler_on(self) -> bool:
        """Whether cooler is on."""
        return bool(self._values.get("cooler_rbv", 0))

    @property
    def status(self) -> str:
        """Current cooler status."""
        val = self._values.get("temp_status", 0)
        return COOLER_STATUS.get(int(val), "Unknown")

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        return {
            "widget_type": "CoolerPanel",
            "temperature": self.temperature,
            "setpoint": self.setpoint,
            "is_cooler_on": self.is_cooler_on,
            "status": self.status,
            "available_actions": [
                {"name": "set_cooler_on", "args": ["on"], "description": "Turn cooler on/off"},
                {"name": "set_temperature_setpoint", "args": ["temp"], "description": "Set temperature setpoint"},
            ],
        }

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        self._disconnect_pvs()
        super().closeEvent(event)
