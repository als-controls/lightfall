"""
PVLineEdit widget - text input for editing PV values.

Supports numeric and string PVs with validation and formatting.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Property, Signal, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from lightfall.epics.widgets.base import EpicsWidget
from lightfall.epics.widgets.style import WidgetStyles


class PVLineEdit(EpicsWidget):
    """
    A line edit widget for entering and displaying EPICS PV values.

    Features:
    - Text input for numeric and string PVs
    - Optional units display
    - Enter key or focus-out to write value
    - Input validation based on PV type and limits
    - Visual feedback for modified but not-yet-written values

    Attributes:
        show_units: Whether to display the PV units.
        precision: Number of decimal places for float display.
        write_on_enter: Write value when Enter is pressed.
        write_on_focus_out: Write value when focus leaves the widget.

    Signals:
        editing_finished: Emitted when editing is complete (Enter or focus out).
        value_written: Emitted after a value is successfully written.

    Example:
        >>> edit = PVLineEdit("MY:PV:SETPOINT")
        >>> edit.write_on_enter = True
    """

    widget_type: ClassVar[str] = "PVLineEdit"
    widget_description: ClassVar[str] = "Text input for editing PV values with validation"

    editing_finished = Signal()
    value_written = Signal(object)

    def __init__(
        self,
        pv_name: str = "",
        parent: QWidget | None = None,
        show_units: bool = True,
        precision: int | None = None,
        write_on_enter: bool = True,
        write_on_focus_out: bool = False,
    ) -> None:
        """
        Initialize the PV line edit.

        Args:
            pv_name: The EPICS PV name to connect to.
            parent: Optional Qt parent widget.
            show_units: Whether to show units after the input.
            precision: Decimal places for float values.
            write_on_enter: Write value when Enter is pressed.
            write_on_focus_out: Write value when focus leaves.
        """
        self._show_units = show_units
        self._precision = precision
        self._write_on_enter = write_on_enter
        self._write_on_focus_out = write_on_focus_out
        self._units: str = ""
        self._modified = False

        super().__init__(pv_name, parent, readonly=False)

        # Create layout and widgets
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._line_edit = QLineEdit()
        self._line_edit.setObjectName("line_edit")
        self._layout.addWidget(self._line_edit)

        self._units_label = QLabel()
        self._units_label.setObjectName("units_label")
        self._layout.addWidget(self._units_label)
        self._units_label.setVisible(show_units)

        # Connect signals
        self._line_edit.textChanged.connect(self._on_text_changed)
        self._line_edit.returnPressed.connect(self._on_return_pressed)
        self._line_edit.editingFinished.connect(self._on_editing_finished)

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

    @Property(bool)
    def write_on_enter(self) -> bool:
        """Whether to write value when Enter is pressed."""
        return self._write_on_enter

    @write_on_enter.setter
    def write_on_enter(self, value: bool) -> None:
        self._write_on_enter = value

    @Property(bool)
    def write_on_focus_out(self) -> bool:
        """Whether to write value when focus leaves the widget."""
        return self._write_on_focus_out

    @write_on_focus_out.setter
    def write_on_focus_out(self, value: bool) -> None:
        self._write_on_focus_out = value

    # Implementation of abstract methods

    def _update_display(self) -> None:
        """Update the line edit text from the current PV value."""
        if self._modified:
            # Don't override user edits
            return

        if self._value is None:
            self._line_edit.setText("")
            return

        text = self._format_value(self._value)
        self._line_edit.blockSignals(True)
        self._line_edit.setText(text)
        self._line_edit.blockSignals(False)

    def _get_widget_value(self) -> Any:
        """Get the current value from the line edit."""
        text = self._line_edit.text().strip()

        # Try to convert to appropriate type
        if self._value is not None:
            try:
                if isinstance(self._value, int):
                    return int(text)
                elif isinstance(self._value, float):
                    return float(text)
            except ValueError:
                pass

        return text

    def _set_widget_value(self, value: Any) -> None:
        """Set the line edit text."""
        self._value = value
        self._modified = False
        self._update_display()

    def _format_value(self, value: Any) -> str:
        """Format a value for display in the line edit."""
        if isinstance(value, float):
            prec = self._precision
            if prec is None and self._pv is not None:
                prec = self._pv.metadata.get("precision", 6)
            if prec is not None:
                return f"{value:.{prec}f}"
        return str(value)

    def _update_readonly_state(self) -> None:
        """Update line edit enabled state based on readonly property and connection."""
        self._line_edit.setReadOnly(self._readonly or not self._connected)

    # Event handlers

    @Slot()
    def _on_text_changed(self) -> None:
        """Handle text changes to track modification state."""
        self._modified = True
        self._update_modified_style()

    @Slot()
    def _on_return_pressed(self) -> None:
        """Handle Enter key press."""
        if self._write_on_enter and self._modified:
            self._write_current_value()

    @Slot()
    def _on_editing_finished(self) -> None:
        """Handle editing finished (focus out or Enter)."""
        self.editing_finished.emit()
        if self._write_on_focus_out and self._modified:
            self._write_current_value()

    def _write_current_value(self) -> None:
        """Write the current widget value to the PV."""
        if not self._connected or self._readonly:
            return

        try:
            value = self._get_widget_value()
            self.write_value(value)
            self._modified = False
            self._update_modified_style()
            self.value_written.emit(value)
        except Exception:
            # Revert to PV value on error
            self._modified = False
            self._update_display()

    def _update_modified_style(self) -> None:
        """Update styling to indicate modified state."""
        if self._modified:
            self._line_edit.setStyleSheet(WidgetStyles.modified())
        else:
            self._line_edit.setStyleSheet("")

    def _on_pv_metadata_changed(self, metadata: dict[str, Any]) -> None:
        """Handle metadata changes."""
        super()._on_pv_metadata_changed(metadata)

        self._units = metadata.get("units", "")
        self._units_label.setText(self._units)
        self._units_label.setVisible(self._show_units and bool(self._units))

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get line edit-specific introspection data."""
        return {
            "show_units": self._show_units,
            "units": self._units,
            "precision": self._precision,
            "write_on_enter": self._write_on_enter,
            "write_on_focus_out": self._write_on_focus_out,
            "current_text": self._line_edit.text(),
            "modified": self._modified,
            "editable": not self._readonly and self._connected,
        }
