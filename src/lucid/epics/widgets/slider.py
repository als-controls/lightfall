"""
PVSlider widget - slider for numeric PV values.

Provides visual analog control with configurable range and precision.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Property, Signal, Slot, Qt
from PySide6.QtWidgets import QSlider, QWidget, QHBoxLayout, QLabel

from lucid.epics.widgets.base import EpicsWidget


class PVSlider(EpicsWidget):
    """
    A slider widget for numeric EPICS PV values.

    Features:
    - Horizontal or vertical orientation
    - Auto-configures range from PV limits
    - Optional value display label
    - Configurable write behavior (on release or continuous)

    Attributes:
        minimum: Minimum slider value.
        maximum: Maximum slider value.
        show_value: Whether to display current value as text.
        write_on_release: If True, only write when slider is released.
        orientation: Qt.Horizontal or Qt.Vertical.

    Signals:
        slider_moved: Emitted when slider position changes.
        value_written: Emitted after value is written to PV.

    Example:
        >>> slider = PVSlider("MY:SETPOINT")
        >>> slider.minimum = 0.0
        >>> slider.maximum = 100.0
        >>> slider.show_value = True
    """

    widget_type: ClassVar[str] = "PVSlider"
    widget_description: ClassVar[str] = "Slider for analog PV control with range limits"

    slider_moved = Signal(float)
    value_written = Signal(object)

    # Slider uses integers internally; we scale by this factor for precision
    _SCALE_FACTOR = 1000

    def __init__(
        self,
        pv_name: str = "",
        parent: QWidget | None = None,
        minimum: float = 0.0,
        maximum: float = 100.0,
        show_value: bool = True,
        write_on_release: bool = True,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
    ) -> None:
        """
        Initialize the PV slider.

        Args:
            pv_name: The EPICS PV name to connect to.
            parent: Optional Qt parent widget.
            minimum: Minimum value.
            maximum: Maximum value.
            show_value: Whether to show value as text.
            write_on_release: Only write when slider is released.
            orientation: Horizontal or Vertical orientation.
        """
        self._minimum = minimum
        self._maximum = maximum
        self._show_value = show_value
        self._write_on_release = write_on_release
        self._orientation = orientation
        self._precision: int | None = None
        self._units: str = ""
        self._updating_from_pv = False
        self._pending_write = False

        super().__init__(pv_name, parent, readonly=False)

        # Create layout
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # Create slider
        self._slider = QSlider(orientation)
        self._slider.setObjectName("slider")
        self._update_slider_range()
        self._layout.addWidget(self._slider)

        # Create value label
        self._value_label = QLabel()
        self._value_label.setObjectName("value_label")
        self._value_label.setMinimumWidth(60)
        self._layout.addWidget(self._value_label)
        self._value_label.setVisible(show_value)

        # Connect signals
        self._slider.valueChanged.connect(self._on_slider_changed)
        self._slider.sliderReleased.connect(self._on_slider_released)

    # Properties

    @Property(float)
    def minimum(self) -> float:
        """Minimum slider value."""
        return self._minimum

    @minimum.setter
    def minimum(self, value: float) -> None:
        self._minimum = value
        self._update_slider_range()

    @Property(float)
    def maximum(self) -> float:
        """Maximum slider value."""
        return self._maximum

    @maximum.setter
    def maximum(self, value: float) -> None:
        self._maximum = value
        self._update_slider_range()

    @Property(bool)
    def show_value(self) -> bool:
        """Whether to display the current value as text."""
        return self._show_value

    @show_value.setter
    def show_value(self, value: bool) -> None:
        self._show_value = value
        self._value_label.setVisible(value)

    @Property(bool)
    def write_on_release(self) -> bool:
        """Whether to only write value when slider is released."""
        return self._write_on_release

    @write_on_release.setter
    def write_on_release(self, value: bool) -> None:
        self._write_on_release = value

    # Private methods

    def _update_slider_range(self) -> None:
        """Update the internal slider range based on min/max values."""
        self._slider.setMinimum(int(self._minimum * self._SCALE_FACTOR))
        self._slider.setMaximum(int(self._maximum * self._SCALE_FACTOR))

    def _slider_to_value(self, slider_value: int) -> float:
        """Convert internal slider value to actual value."""
        return slider_value / self._SCALE_FACTOR

    def _value_to_slider(self, value: float) -> int:
        """Convert actual value to internal slider value."""
        return int(value * self._SCALE_FACTOR)

    def _format_value(self, value: float) -> str:
        """Format value for display in label."""
        prec = self._precision if self._precision is not None else 2
        text = f"{value:.{prec}f}"
        if self._units:
            text += f" {self._units}"
        return text

    # Implementation of abstract methods

    def _update_display(self) -> None:
        """Update the slider position from the current PV value."""
        if self._value is None:
            return

        self._updating_from_pv = True
        try:
            value = float(self._value)
            # Clamp to range
            value = max(self._minimum, min(self._maximum, value))
            self._slider.setValue(self._value_to_slider(value))
            self._value_label.setText(self._format_value(value))
        except (ValueError, TypeError):
            pass
        finally:
            self._updating_from_pv = False

    def _get_widget_value(self) -> float:
        """Get the current slider value."""
        return self._slider_to_value(self._slider.value())

    def _set_widget_value(self, value: Any) -> None:
        """Set the slider position."""
        self._value = value
        self._update_display()

    def _update_readonly_state(self) -> None:
        """Update slider enabled state based on readonly property."""
        self._slider.setEnabled(not self._readonly and self._connected)

    # Event handlers

    @Slot(int)
    def _on_slider_changed(self, slider_value: int) -> None:
        """Handle slider value change."""
        if self._updating_from_pv:
            return

        value = self._slider_to_value(slider_value)
        self._value_label.setText(self._format_value(value))
        self.slider_moved.emit(value)

        if self._write_on_release:
            self._pending_write = True
        elif self._connected and not self._readonly:
            self._write_to_pv(value)

    @Slot()
    def _on_slider_released(self) -> None:
        """Handle slider release."""
        if self._pending_write and self._connected and not self._readonly:
            value = self._get_widget_value()
            self._write_to_pv(value)
            self._pending_write = False

    def _write_to_pv(self, value: float) -> None:
        """Write a value to the PV."""
        try:
            self.write_value(value)
            self.value_written.emit(value)
        except Exception:
            # Revert on error
            self._update_display()

    def _on_pv_metadata_changed(self, metadata: dict[str, Any]) -> None:
        """Handle metadata changes to update range and precision."""
        super()._on_pv_metadata_changed(metadata)

        # Update range from control limits
        if "lower_limit" in metadata:
            self._minimum = float(metadata["lower_limit"])
        if "upper_limit" in metadata:
            self._maximum = float(metadata["upper_limit"])
        self._update_slider_range()

        # Store precision and units
        if "precision" in metadata:
            self._precision = int(metadata["precision"])
        if "units" in metadata:
            self._units = metadata["units"]

        # Re-display with new formatting
        self._update_display()

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get slider-specific introspection data."""
        return {
            "minimum": self._minimum,
            "maximum": self._maximum,
            "current_position": self._get_widget_value(),
            "show_value": self._show_value,
            "displayed_value": self._value_label.text(),
            "write_on_release": self._write_on_release,
            "orientation": "horizontal" if self._orientation == Qt.Orientation.Horizontal else "vertical",
            "precision": self._precision,
            "units": self._units,
            "editable": not self._readonly and self._connected,
        }
