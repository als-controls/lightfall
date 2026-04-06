"""Temperature display panel for PIMTE-style cameras.

Provides read-only temperature display for cameras like Princeton PIMTE
that have temperature sensors but simpler cooling control.

Uses ophyd's uniform signal interface, working with any device that has
the appropriate cam signals (temperature, temperature_setpoint).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Signal
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

    Uses ophyd's uniform signal interface for temperature monitoring.
    Works with any ophyd device that has cam signals for:
    - temperature: Sensor temperature (C)
    - temperature_setpoint: Temperature setpoint (C)

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
            device: Ophyd device with cam temperature signals.
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

        # Sensor temperature (read-only)
        layout.addWidget(QLabel("Sensor:"), row, 0)
        self._sensor_label = QLabel("--- C")
        self._sensor_label.setStyleSheet("font-family: monospace; font-weight: bold;")
        layout.addWidget(self._sensor_label, row, 1)

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

        # Actual setpoint readback
        layout.addWidget(QLabel("Actual SP:"), row, 0)
        self._actual_sp_label = QLabel("--- C")
        self._actual_sp_label.setStyleSheet("font-family: monospace;")
        layout.addWidget(self._actual_sp_label, row, 1)

        # Initial state - disabled
        self._setpoint_spin.setEnabled(False)

    def _connect_signals(self) -> None:
        """Connect to ophyd device signals.

        Connects to the cam component signals for temperature monitoring.
        """
        self._disconnect_signals()

        if self._device is None or not hasattr(self._device, "cam"):
            return

        cam = self._device.cam

        # Signal mapping: ophyd attribute -> internal name
        signal_map = {
            "temperature": "temperature",
            "temperature_setpoint": "setpoint",
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
        self._setpoint_spin.setEnabled(has_temp)

        # Trigger initial display updates
        self._update_temperature_display()
        self._update_setpoint_display()

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
        """Handle ophyd signal value updates.

        Called from ophyd's callback thread — stores value and marshals
        the UI update to the main thread.
        """
        from lucid.utils.threads import invoke_in_main_thread

        # Extract scalar from array if needed
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]

        self._values[name] = value

        invoke_in_main_thread(self._apply_value_update, name)

    def _apply_value_update(self, name: str) -> None:
        """Apply a cached value update to the UI (main thread only)."""
        if name == "temperature":
            self._update_temperature_display()
        elif name == "setpoint":
            self._update_setpoint_display()

    def _update_temperature_display(self) -> None:
        """Update sensor temperature display."""
        value = self._values.get("temperature")
        if value is not None:
            self._sensor_label.setText(f"{float(value):.1f} C")

    def _update_setpoint_display(self) -> None:
        """Update setpoint displays."""
        # Update actual setpoint label
        value = self._values.get("setpoint")
        if value is not None:
            self._actual_sp_label.setText(f"{float(value):.1f} C")

            # Also update spinbox if not focused
            if not self._setpoint_spin.hasFocus():
                self._setpoint_spin.blockSignals(True)
                self._setpoint_spin.setValue(float(value))
                self._setpoint_spin.blockSignals(False)

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

    def _on_setpoint_changed(self) -> None:
        """Handle setpoint change."""
        value = self._setpoint_spin.value()
        self._put_value("temperature_setpoint", value)
        self.setpoint_changed.emit(value)

    # === Public API ===

    def set_temperature_setpoint(self, temp: float) -> None:
        """Set the temperature setpoint.

        Args:
            temp: Temperature in C.
        """
        self._put_value("temperature_setpoint", temp)

    @property
    def temperature(self) -> float | None:
        """Current sensor temperature in C."""
        val = self._values.get("temperature")
        return float(val) if val is not None else None

    @property
    def setpoint(self) -> float | None:
        """Current temperature setpoint in C."""
        val = self._values.get("setpoint")
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
        self._disconnect_signals()
        super().close()
