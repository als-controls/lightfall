"""Temperature display panel for PIMTE-style cameras.

Provides read-only temperature display for cameras like Princeton PIMTE
that have temperature sensors but simpler cooling control.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QWidget,
)

from lucid.utils.logging import logger


class TemperaturePanel(QGroupBox):
    """Temperature display panel for PIMTE-style cameras.

    Provides temperature monitoring and setpoint control for cameras
    that have simpler temperature management than Andor coolers.

    Standard PVs used:
    - Temperature_RBV: Sensor temperature (°C)
    - TemperatureSetPoint: Temperature setpoint (°C)
    - TemperatureSetPoint_RBV: Actual setpoint readback (°C)

    Signals:
        setpoint_changed: Emitted when setpoint is changed.
    """

    setpoint_changed = Signal(float)

    def __init__(
        self,
        prefix: str = "",
        cam_suffix: str = "cam1:",
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the temperature panel.

        Args:
            prefix: EPICS PV prefix (e.g., "13PIMTE1:").
            cam_suffix: Camera plugin suffix (default "cam1:").
            parent: Parent widget.
        """
        super().__init__("Temperature", parent)
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

        # Sensor temperature (read-only)
        layout.addWidget(QLabel("Sensor:"), row, 0)
        self._sensor_label = QLabel("--- °C")
        self._sensor_label.setStyleSheet("font-family: monospace; font-weight: bold;")
        layout.addWidget(self._sensor_label, row, 1)

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

        # Actual setpoint readback
        layout.addWidget(QLabel("Actual SP:"), row, 0)
        self._actual_sp_label = QLabel("--- °C")
        self._actual_sp_label.setStyleSheet("font-family: monospace;")
        layout.addWidget(self._actual_sp_label, row, 1)

        # Initial state - disabled
        self._setpoint_spin.setEnabled(False)

    def _connect_pvs(self) -> None:
        """Connect to temperature PVs."""
        if not self._cam_prefix:
            return

        try:
            from epics_pyside.ca.pv import PV

            # PVs to connect
            pv_fields = {
                "Temperature_RBV": "temperature",
                "TemperatureSetPoint": "setpoint",
                "TemperatureSetPoint_RBV": "setpoint_rbv",
            }

            for field, name in pv_fields.items():
                pv_name = f"{self._cam_prefix}{field}"
                pv = PV(pv_name, parent=self)
                pv.value_changed.connect(lambda v, n=name: self._on_pv_value(n, v))
                pv.connection_changed.connect(lambda c, n=name: self._on_pv_connection(n, c))
                pv.connect_pv()
                self._pvs[name] = pv

        except ImportError:
            logger.warning("epics_pyside not available, temperature panel disabled")

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
        if name == "temperature":
            self._update_temperature_display()
        elif name == "setpoint_rbv":
            self._update_setpoint_display()

    @Slot(str, bool)
    def _on_pv_connection(self, name: str, connected: bool) -> None:
        """Handle PV connection state changes."""
        if connected:
            self._connected_pvs.add(name)
        else:
            self._connected_pvs.discard(name)

        # Enable controls if we have temperature readback
        is_connected = "temperature" in self._connected_pvs
        self._setpoint_spin.setEnabled(is_connected)

    def _update_temperature_display(self) -> None:
        """Update sensor temperature display."""
        value = self._values.get("temperature")
        if value is not None:
            self._sensor_label.setText(f"{float(value):.1f} °C")

    def _update_setpoint_display(self) -> None:
        """Update setpoint displays."""
        # Update actual setpoint label
        value = self._values.get("setpoint_rbv")
        if value is not None:
            self._actual_sp_label.setText(f"{float(value):.1f} °C")

            # Also update spinbox if not focused
            if not self._setpoint_spin.hasFocus():
                self._setpoint_spin.blockSignals(True)
                self._setpoint_spin.setValue(float(value))
                self._setpoint_spin.blockSignals(False)

    def _on_setpoint_changed(self) -> None:
        """Handle setpoint change."""
        value = self._setpoint_spin.value()
        if "setpoint" in self._pvs:
            self._pvs["setpoint"].put(value)
            self.setpoint_changed.emit(value)

    # === Public API ===

    def set_temperature_setpoint(self, temp: float) -> None:
        """Set the temperature setpoint.

        Args:
            temp: Temperature in °C.
        """
        if "setpoint" in self._pvs:
            self._pvs["setpoint"].put(temp)

    @property
    def temperature(self) -> float | None:
        """Current sensor temperature in °C."""
        val = self._values.get("temperature")
        return float(val) if val is not None else None

    @property
    def setpoint(self) -> float | None:
        """Current temperature setpoint in °C."""
        val = self._values.get("setpoint_rbv")
        return float(val) if val is not None else None

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        return {
            "widget_type": "TemperaturePanel",
            "temperature": self.temperature,
            "setpoint": self.setpoint,
            "available_actions": [
                {"name": "set_temperature_setpoint", "args": ["temp"], "description": "Set temperature setpoint"},
            ],
        }

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        self._disconnect_pvs()
        super().closeEvent(event)
