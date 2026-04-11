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

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QWidget,
)

from lucid.epics.widgets.ophyd_combobox import OphydComboBox
from lucid.epics.widgets.ophyd_label import OphydLabel
from lucid.epics.widgets.ophyd_spinbox import OphydSpinBox
from lucid.epics.widgets.status_indicator import StatusIndicator
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


class CoolerPanel(QGroupBox):
    """Cooler control panel for Andor-style cameras.

    Uses ophyd widgets (OphydComboBox, OphydSpinBox, OphydLabel) bound
    directly to cam signals for automatic subscription and display.
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

        self._setup_ui()

        if device is not None:
            self._bind_signals()

    def set_device(self, device: Any) -> None:
        """Set the ophyd device and reconnect signals.

        Args:
            device: Ophyd device with cam cooler signals.
        """
        if device != self._device:
            self._unbind_signals()
            self._device = device
            if device is not None:
                self._bind_signals()

    def _setup_ui(self) -> None:
        """Create the panel UI."""
        layout = QGridLayout(self)
        layout.setSpacing(8)

        row = 0

        # Cooler state toggle
        layout.addWidget(QLabel("State:"), row, 0)
        self._cooler_combo = OphydComboBox(write_on_change=True)
        self._cooler_combo.set_items(["Off", "On"])
        self._cooler_combo.selection_changed.connect(self._on_cooler_state_changed)
        layout.addWidget(self._cooler_combo, row, 1)

        row += 1

        # Temperature setpoint
        layout.addWidget(QLabel("Setpoint:"), row, 0)
        self._setpoint_spin = OphydSpinBox(
            minimum=-100.0, maximum=50.0, decimals=1, write_on_change=True,
        )
        self._setpoint_spin.value_written.connect(self._on_setpoint_written)
        layout.addWidget(self._setpoint_spin, row, 1)

        row += 1

        # Actual temperature
        layout.addWidget(QLabel("Actual:"), row, 0)
        self._temp_label = OphydLabel(precision=1)
        self._temp_label.setStyleSheet("font-family: monospace; font-weight: bold;")
        layout.addWidget(self._temp_label, row, 1)

        row += 1

        # Cooler status with indicator
        layout.addWidget(QLabel("Status:"), row, 0)
        status_widget = QWidget()
        status_layout = QGridLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)

        self._status_indicator = StatusIndicator(size=12)
        status_layout.addWidget(self._status_indicator, 0, 0)

        self._status_label = QLabel("---")
        status_layout.addWidget(self._status_label, 0, 1)

        layout.addWidget(status_widget, row, 1)

        # Initial state - disabled until device is bound
        self._set_controls_enabled(False)

    def _bind_signals(self) -> None:
        """Bind ophyd widget signals to the device cam component."""
        if self._device is None or not hasattr(self._device, "cam"):
            return

        cam = self._device.cam

        if hasattr(cam, "andor_cooler"):
            self._cooler_combo.signal = cam.andor_cooler
        if hasattr(cam, "andor_temp_setpoint"):
            self._setpoint_spin.signal = cam.andor_temp_setpoint
        if hasattr(cam, "temperature"):
            self._temp_label.signal = cam.temperature

        # temperature_status still needs manual subscription for the
        # StatusIndicator color logic — OphydLabel can't drive that
        if hasattr(cam, "temperature_status"):
            try:
                self._status_sub_signal = cam.temperature_status
                self._status_sub_id = cam.temperature_status.subscribe(
                    self._on_status_value,
                )
            except Exception as e:
                logger.debug(f"Failed to subscribe to temperature_status: {e}")
                self._status_sub_signal = None
                self._status_sub_id = None
        else:
            self._status_sub_signal = None
            self._status_sub_id = None

        self._set_controls_enabled(True)

    def _unbind_signals(self) -> None:
        """Unbind all ophyd widget signals."""
        self._cooler_combo.signal = None
        self._setpoint_spin.signal = None
        self._temp_label.signal = None

        # Unsubscribe status manually
        sig = getattr(self, "_status_sub_signal", None)
        sub_id = getattr(self, "_status_sub_id", None)
        if sig is not None and sub_id is not None:
            try:
                sig.unsubscribe(sub_id)
            except Exception:
                pass
        self._status_sub_signal = None
        self._status_sub_id = None

        self._status_label.setText("---")
        self._status_indicator.set_state("off")

    def _on_status_value(self, value: Any = None, **kwargs: Any) -> None:
        """Handle temperature_status subscription callback (background thread)."""
        from lucid.utils.threads import invoke_in_main_thread

        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]

        invoke_in_main_thread(self._update_status_display, value)

    def _update_status_display(self, value: Any) -> None:
        """Update cooler status display (main thread)."""
        if value is None:
            return
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

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable controls."""
        self._cooler_combo.readonly = not enabled
        self._setpoint_spin.readonly = not enabled

    def _on_cooler_state_changed(self, index: int, _text: str) -> None:
        """Handle cooler state change from OphydComboBox."""
        self.cooler_state_changed.emit(bool(index))

    def _on_setpoint_written(self, value: object) -> None:
        """Handle setpoint written from OphydSpinBox."""
        self.setpoint_changed.emit(float(value))

    # === Public API ===

    def set_cooler_on(self, on: bool) -> None:
        """Turn cooler on or off.

        Args:
            on: True to turn on, False to turn off.
        """
        try:
            self._cooler_combo.write_value(1 if on else 0)
        except Exception as e:
            logger.warning(f"Failed to set cooler: {e}")

    def set_temperature_setpoint(self, temp: float) -> None:
        """Set the temperature setpoint.

        Args:
            temp: Temperature in C.
        """
        try:
            self._setpoint_spin.write_value(temp)
        except Exception as e:
            logger.warning(f"Failed to set temperature setpoint: {e}")

    @property
    def temperature(self) -> float | None:
        """Current actual temperature in C."""
        val = self._temp_label._value
        return float(val) if val is not None else None

    @property
    def setpoint(self) -> float | None:
        """Current temperature setpoint in C."""
        val = self._setpoint_spin._value
        return float(val) if val is not None else None

    @property
    def is_cooler_on(self) -> bool:
        """Whether cooler is on."""
        val = self._cooler_combo._value
        return bool(val) if val is not None else False

    @property
    def status(self) -> str:
        """Current cooler status."""
        return self._status_label.text()

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
        self._unbind_signals()
        super().close()
