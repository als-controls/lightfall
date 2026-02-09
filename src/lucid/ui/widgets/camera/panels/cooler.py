"""Cooler control panel for Andor-style cameras.

Provides controls for:
- Cooler on/off
- Temperature setpoint
- Actual temperature readback
- Cooler status display

Uses ophyd's uniform signal interface, working with any device that has
the appropriate cam signals (andor_cooler, temperature_setpoint, etc.).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Signal
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

    Uses ophyd's uniform signal interface to control cooler functionality.
    Works with any ophyd device that has cam signals for:
    - andor_cooler: Cooler enable (0=Off, 1=On)
    - andor_temp_setpoint: Temperature setpoint (C)
    - temperature: Actual temperature (C)
    - temperature_status: Cooler status

    Signals:
        cooler_state_changed: Emitted when cooler on/off changes.
        setpoint_changed: Emitted when setpoint is changed.
    """

    cooler_state_changed = Signal(bool)  # True = On
    setpoint_changed = Signal(float)

    def __init__(
        self,
        device: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the cooler panel.

        Args:
            device: Ophyd device with cam cooler signals.
            parent: Parent widget.
        """
        super().__init__("Cooler", parent)
        self._device = device

        # Signal subscriptions: list of (signal, subscription_id) tuples
        self._subscriptions: list[tuple[Any, int]] = []

        # Cached values from device signals
        self._values: dict[str, Any] = {}

        self._setup_ui()

        if device is not None:
            QTimer.singleShot(0, self._connect_signals)

    def set_device(self, device: Any) -> None:
        """Set the ophyd device and reconnect signals.

        Args:
            device: Ophyd device with cam cooler signals.
        """
        if device != self._device:
            self._disconnect_signals()
            self._device = device
            if device is not None:
                self._connect_signals()

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
        self._setpoint_spin.setSuffix(" C")
        self._setpoint_spin.editingFinished.connect(self._on_setpoint_changed)
        layout.addWidget(self._setpoint_spin, row, 1)

        row += 1

        # Actual temperature
        layout.addWidget(QLabel("Actual:"), row, 0)
        self._temp_label = QLabel("--- C")
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

    def _connect_signals(self) -> None:
        """Connect to ophyd device signals.

        Connects to the cam component signals for cooler control.
        """
        self._disconnect_signals()

        if self._device is None or not hasattr(self._device, "cam"):
            return

        cam = self._device.cam

        # Signal mapping: ophyd attribute -> internal name
        signal_map = {
            "andor_cooler": "cooler",
            "andor_temp_setpoint": "setpoint",
            "temperature": "temperature",
            "temperature_status": "temp_status",
        }

        def make_callback(name: str):
            """Create a callback that updates the specified value name."""
            def callback(value, **kwargs):
                self._on_value_changed(name, value)
            return callback

        for attr, name in signal_map.items():
            if hasattr(cam, attr):
                signal = getattr(cam, attr)

                # Get initial value
                try:
                    value = signal.get()
                    self._values[name] = value
                except Exception as e:
                    logger.debug(f"Failed to get initial value for {attr}: {e}")

                # Subscribe for updates
                try:
                    sub_id = signal.subscribe(make_callback(name))
                    self._subscriptions.append((signal, sub_id))
                except Exception as e:
                    logger.debug(f"Failed to subscribe to {attr}: {e}")

        # Enable controls if we have temperature signal
        has_temp = "temperature" in self._values
        self._set_controls_enabled(has_temp)

        # Trigger initial display updates
        self._update_cooler_display()
        self._update_setpoint_display()
        self._update_temperature_display()
        self._update_status_display()

    def _disconnect_signals(self) -> None:
        """Disconnect all ophyd signal subscriptions."""
        for signal, sub_id in self._subscriptions:
            try:
                signal.unsubscribe(sub_id)
            except Exception:
                pass
        self._subscriptions.clear()
        self._values.clear()

    def _on_value_changed(self, name: str, value: Any) -> None:
        """Handle ophyd signal value updates."""
        # Extract scalar from array if needed
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]

        self._values[name] = value

        # Update UI
        if name == "cooler":
            self._update_cooler_display()
        elif name == "setpoint":
            self._update_setpoint_display()
        elif name == "temperature":
            self._update_temperature_display()
        elif name == "temp_status":
            self._update_status_display()

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable controls."""
        self._cooler_combo.setEnabled(enabled)
        self._setpoint_spin.setEnabled(enabled)

    def _update_cooler_display(self) -> None:
        """Update cooler state display."""
        value = self._values.get("cooler")
        if value is not None:
            idx = int(value)
            self._cooler_combo.blockSignals(True)
            self._cooler_combo.setCurrentIndex(min(idx, 1))
            self._cooler_combo.blockSignals(False)

    def _update_setpoint_display(self) -> None:
        """Update setpoint display."""
        if not self._setpoint_spin.hasFocus():
            value = self._values.get("setpoint")
            if value is not None:
                self._setpoint_spin.blockSignals(True)
                self._setpoint_spin.setValue(float(value))
                self._setpoint_spin.blockSignals(False)

    def _update_temperature_display(self) -> None:
        """Update actual temperature display."""
        value = self._values.get("temperature")
        if value is not None:
            self._temp_label.setText(f"{float(value):.1f} C")

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

    def _put_value(self, name: str, value: Any) -> None:
        """Set a value on the ophyd device.

        Args:
            name: Signal attribute name on the cam component.
            value: Value to set.
        """
        if self._device is None:
            return

        cam = getattr(self._device, "cam", None)
        if cam is not None and hasattr(cam, name):
            signal = getattr(cam, name)
            try:
                signal.set(value).wait(timeout=5.0)
            except Exception as e:
                logger.warning(f"Failed to set {name}: {e}")

    def _on_cooler_state_changed(self, index: int) -> None:
        """Handle cooler state change."""
        self._put_value("andor_cooler", index)
        self.cooler_state_changed.emit(bool(index))

    def _on_setpoint_changed(self) -> None:
        """Handle setpoint change."""
        value = self._setpoint_spin.value()
        self._put_value("andor_temp_setpoint", value)
        self.setpoint_changed.emit(value)

    # === Public API ===

    def set_cooler_on(self, on: bool) -> None:
        """Turn cooler on or off.

        Args:
            on: True to turn on, False to turn off.
        """
        self._put_value("andor_cooler", 1 if on else 0)

    def set_temperature_setpoint(self, temp: float) -> None:
        """Set the temperature setpoint.

        Args:
            temp: Temperature in C.
        """
        self._put_value("andor_temp_setpoint", temp)

    @property
    def temperature(self) -> float | None:
        """Current actual temperature in C."""
        val = self._values.get("temperature")
        return float(val) if val is not None else None

    @property
    def setpoint(self) -> float | None:
        """Current temperature setpoint in C."""
        val = self._values.get("setpoint")
        return float(val) if val is not None else None

    @property
    def is_cooler_on(self) -> bool:
        """Whether cooler is on."""
        return bool(self._values.get("cooler", 0))

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

    def close(self) -> None:
        """Clean up on close."""
        self._disconnect_signals()
        super().close()
