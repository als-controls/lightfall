"""Temperature display panel for PIMTE-style cameras.

Provides read-only temperature display for cameras like Princeton PIMTE
that have temperature sensors but simpler cooling control.

Uses ophyd's uniform signal interface, working with any device that has
the appropriate cam signals (temperature, temperature_setpoint).
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

from lucid.epics.widgets.ophyd_label import OphydLabel
from lucid.epics.widgets.ophyd_spinbox import OphydSpinBox
from lucid.utils.logging import logger


class TemperaturePanel(QGroupBox):
    """Temperature display panel for PIMTE-style cameras.

    Uses ophyd widgets (OphydSpinBox, OphydLabel) bound directly to cam
    signals for automatic subscription and display.

    Works with the standard ophyd AreaDetectorCam interface:
    - ``temperature_actual`` (EpicsSignal): the sensor reading
    - ``temperature`` (EpicsSignalWithRBV): the setpoint, where ``put()``
      writes the setpoint PV and ``get()`` returns the setpoint readback.

    Signals:
        setpoint_changed: Emitted when setpoint is changed.
    """

    setpoint_changed = Signal(float)

    def __init__(
        self,
        device: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the temperature panel.

        Args:
            device: Ophyd device with cam temperature signals.
            parent: Parent widget.
        """
        super().__init__("Temperature", parent)
        self._device = device

        self._setup_ui()

        if device is not None:
            self._bind_signals()

    def set_device(self, device: Any) -> None:
        """Set the ophyd device and reconnect signals.

        Args:
            device: Ophyd device with cam temperature signals.
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

        # Sensor temperature (read-only)
        layout.addWidget(QLabel("Sensor:"), row, 0)
        self._sensor_label = OphydLabel(precision=1)
        self._sensor_label.setStyleSheet("font-family: monospace; font-weight: bold;")
        layout.addWidget(self._sensor_label, row, 1)

        row += 1

        # Temperature setpoint
        layout.addWidget(QLabel("Setpoint:"), row, 0)
        self._setpoint_spin = OphydSpinBox(
            minimum=-100.0, maximum=50.0, decimals=1, write_on_change=True,
        )
        self._setpoint_spin.value_written.connect(self._on_setpoint_written)
        layout.addWidget(self._setpoint_spin, row, 1)

        row += 1

        # Actual setpoint readback
        layout.addWidget(QLabel("Actual SP:"), row, 0)
        self._actual_sp_label = OphydLabel(precision=1)
        self._actual_sp_label.setStyleSheet("font-family: monospace;")
        layout.addWidget(self._actual_sp_label, row, 1)

        # Initial state - disabled
        self._setpoint_spin.readonly = True

    def _bind_signals(self) -> None:
        """Bind ophyd widget signals to the device cam component."""
        if self._device is None or not hasattr(self._device, "cam"):
            return

        cam = self._device.cam

        # Sensor reading (AreaDetectorCam.temperature_actual -> *TemperatureActual*).
        # Fall back to 'temperature' on non-AreaDetector devices that expose
        # the sensor under that name.
        sensor_sig = getattr(cam, "temperature_actual", None) or getattr(cam, "temperature", None)
        if sensor_sig is not None and hasattr(cam, "temperature_actual"):
            self._sensor_label.signal = cam.temperature_actual
        elif sensor_sig is not None:
            self._sensor_label.signal = sensor_sig

        # Setpoint (AreaDetectorCam.temperature is EpicsSignalWithRBV:
        # .put() -> *Temperature*, .get() -> *Temperature_RBV*).
        # When temperature_actual exists, 'temperature' is the setpoint.
        setpoint_sig = None
        if hasattr(cam, "temperature_setpoint"):
            setpoint_sig = cam.temperature_setpoint
        elif hasattr(cam, "temperature_actual") and hasattr(cam, "temperature"):
            setpoint_sig = cam.temperature

        if setpoint_sig is not None:
            self._setpoint_spin.signal = setpoint_sig
            self._actual_sp_label.signal = setpoint_sig
            self._setpoint_spin.readonly = False
        else:
            self._setpoint_spin.readonly = True

    def _unbind_signals(self) -> None:
        """Unbind all ophyd widget signals."""
        self._sensor_label.signal = None
        self._setpoint_spin.signal = None
        self._actual_sp_label.signal = None

        self._setpoint_spin.readonly = True

    def _on_setpoint_written(self, value: object) -> None:
        """Handle setpoint written from OphydSpinBox."""
        self.setpoint_changed.emit(float(value))

    # === Public API ===

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
        """Current sensor temperature in C."""
        val = self._sensor_label._value
        return float(val) if val is not None else None

    @property
    def setpoint(self) -> float | None:
        """Current temperature setpoint in C."""
        val = self._setpoint_spin._value
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

    def close(self) -> None:
        """Clean up on close."""
        self._unbind_signals()
        super().close()
