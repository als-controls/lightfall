"""
PVLabel widget - displays a PV value as read-only text.

This is the simplest display widget, showing the current PV value
with optional units and formatting.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Property
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from lucid.epics.widgets.base import EpicsWidget


class PVLabel(EpicsWidget):
    """
    A label widget that displays an EPICS PV value.

    Features:
    - Automatic value updates from PV subscription
    - Optional units display
    - Configurable precision for floating-point values
    - Connection state indication via styling

    Attributes:
        show_units: Whether to display the PV units.
        precision: Number of decimal places for float values.
        format_string: Custom format string (overrides precision).

    Example:
        >>> label = PVLabel("MY:PV:VALUE")
        >>> label.show_units = True
        >>> label.precision = 3
    """

    widget_type: ClassVar[str] = "PVLabel"
    widget_description: ClassVar[str] = "Read-only label displaying a PV value with optional units"

    def __init__(
        self,
        pv_name: str = "",
        parent: QWidget | None = None,
        show_units: bool = True,
        precision: int | None = None,
    ) -> None:
        """
        Initialize the PV label.

        Args:
            pv_name: The EPICS PV name to display.
            parent: Optional Qt parent widget.
            show_units: Whether to show units after the value.
            precision: Decimal places for float values (None = use PV precision).
        """
        self._show_units = show_units
        self._precision = precision
        self._format_string: str | None = None
        self._units: str = ""

        super().__init__(pv_name, parent, readonly=True)

        # Create the label widget
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._value_label = QLabel()
        self._value_label.setObjectName("value_label")
        self._layout.addWidget(self._value_label)

        self._units_label = QLabel()
        self._units_label.setObjectName("units_label")
        self._layout.addWidget(self._units_label)
        self._units_label.setVisible(show_units)

    # Properties

    @Property(bool)
    def show_units(self) -> bool:
        """Whether to display units after the value."""
        return self._show_units

    @show_units.setter
    def show_units(self, value: bool) -> None:
        self._show_units = value
        self._units_label.setVisible(value and bool(self._units))

    @Property(int)
    def precision(self) -> int | None:
        """Number of decimal places for float values."""
        return self._precision

    @precision.setter
    def precision(self, value: int | None) -> None:
        self._precision = value
        self._update_display()

    @Property(str)
    def format_string(self) -> str | None:
        """Custom format string for value display."""
        return self._format_string

    @format_string.setter
    def format_string(self, value: str | None) -> None:
        self._format_string = value
        self._update_display()

    # Implementation of abstract methods

    def _update_display(self) -> None:
        """Update the label text from the current PV value."""
        if self._value is None:
            self._value_label.setText("---")
            return

        # Format the value
        text = self._format_value(self._value)
        self._value_label.setText(text)

    def _get_widget_value(self) -> Any:
        """Get the current displayed value."""
        return self._value

    def _set_widget_value(self, value: Any) -> None:
        """Set the displayed value."""
        self._value = value
        self._update_display()

    def _format_value(self, value: Any) -> str:
        """
        Format a value for display.

        Args:
            value: The value to format.

        Returns:
            Formatted string representation.
        """
        if self._format_string:
            try:
                return self._format_string.format(value)
            except (ValueError, KeyError):
                pass

        if isinstance(value, float):
            prec = self._precision
            if prec is None and self._pv is not None:
                prec = self._pv.metadata.get("precision", 6)
            if prec is not None:
                return f"{value:.{prec}f}"

        return str(value)

    def _on_pv_metadata_changed(self, metadata: dict[str, Any]) -> None:
        """Handle metadata changes to update units display."""
        super()._on_pv_metadata_changed(metadata)

        self._units = metadata.get("units", "")
        self._units_label.setText(self._units)
        self._units_label.setVisible(self._show_units and bool(self._units))

        # Re-format value if precision changed
        if "precision" in metadata and self._precision is None:
            self._update_display()

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get label-specific introspection data."""
        return {
            "show_units": self._show_units,
            "units": self._units,
            "precision": self._precision,
            "format_string": self._format_string,
            "displayed_text": self._value_label.text(),
        }
