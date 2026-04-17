"""
PVCheckBox widget - checkbox for binary/boolean PVs.

Supports both binary (0/1) and enum PVs with two states.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Property, Qt, Signal, Slot
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QWidget

from lucid.epics.widgets.base import EpicsWidget


class PVCheckBox(EpicsWidget):
    """
    A checkbox widget for binary EPICS PV values.

    Features:
    - Toggle between two states (checked/unchecked)
    - Configurable values for each state
    - Optional text labels for checked/unchecked states
    - Auto-write on state change

    Attributes:
        checked_value: Value to write when checked (default: 1).
        unchecked_value: Value to write when unchecked (default: 0).
        write_on_change: Whether to write to PV when toggled.

    Signals:
        toggled: Emitted when checkbox state changes.
        value_written: Emitted after value is written to PV.

    Example:
        >>> cb = PVCheckBox("MY:ENABLE:PV")
        >>> cb.setText("Enable Feature")
        >>> cb.checked_value = "ON"
        >>> cb.unchecked_value = "OFF"
    """

    widget_type: ClassVar[str] = "PVCheckBox"
    widget_description: ClassVar[str] = "Checkbox for binary PV toggling"

    toggled = Signal(bool)
    value_written = Signal(object)

    def __init__(
        self,
        pv_name: str = "",
        parent: QWidget | None = None,
        text: str = "",
        checked_value: Any = 1,
        unchecked_value: Any = 0,
        write_on_change: bool = True,
    ) -> None:
        """
        Initialize the PV checkbox.

        Args:
            pv_name: The EPICS PV name to connect to.
            parent: Optional Qt parent widget.
            text: Label text for the checkbox.
            checked_value: Value to write when checked.
            unchecked_value: Value to write when unchecked.
            write_on_change: Write to PV when toggled.
        """
        self._checked_value = checked_value
        self._unchecked_value = unchecked_value
        self._write_on_change = write_on_change
        self._updating_from_pv = False

        super().__init__(pv_name, parent, readonly=False)

        # Create layout and checkbox
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._checkbox = QCheckBox(text)
        self._checkbox.setObjectName("checkbox")
        self._layout.addWidget(self._checkbox)

        # Connect signals
        self._checkbox.stateChanged.connect(self._on_state_changed)

    # Properties

    @Property(object)
    def checked_value(self) -> Any:
        """Value to write when checkbox is checked."""
        return self._checked_value

    @checked_value.setter
    def checked_value(self, value: Any) -> None:
        self._checked_value = value

    @Property(object)
    def unchecked_value(self) -> Any:
        """Value to write when checkbox is unchecked."""
        return self._unchecked_value

    @unchecked_value.setter
    def unchecked_value(self, value: Any) -> None:
        self._unchecked_value = value

    @Property(bool)
    def write_on_change(self) -> bool:
        """Whether to write to PV when toggled."""
        return self._write_on_change

    @write_on_change.setter
    def write_on_change(self, value: bool) -> None:
        self._write_on_change = value

    # Public methods

    def setText(self, text: str) -> None:
        """
        Set the checkbox label text.

        Args:
            text: The label text to display.
        """
        self._checkbox.setText(text)

    def text(self) -> str:
        """Get the checkbox label text."""
        return self._checkbox.text()

    def isChecked(self) -> bool:
        """Check if the checkbox is currently checked."""
        return self._checkbox.isChecked()

    def setChecked(self, checked: bool) -> None:
        """
        Set the checkbox state.

        Args:
            checked: Whether the checkbox should be checked.
        """
        self._checkbox.setChecked(checked)

    # Implementation of abstract methods

    def _update_display(self) -> None:
        """Update the checkbox state from the current PV value."""
        if self._value is None:
            return

        self._updating_from_pv = True
        try:
            # Determine if value matches checked state
            checked = self._value_matches_checked(self._value)
            self._checkbox.setChecked(checked)
        finally:
            self._updating_from_pv = False

    def _value_matches_checked(self, value: Any) -> bool:
        """
        Determine if a PV value corresponds to the checked state.

        Args:
            value: The PV value to check.

        Returns:
            True if value matches checked_value.
        """
        # Direct comparison
        if value == self._checked_value:
            return True
        if value == self._unchecked_value:
            return False

        # Try numeric comparison
        try:
            return float(value) == float(self._checked_value)
        except (ValueError, TypeError):
            pass

        # Try string comparison
        return str(value).lower() == str(self._checked_value).lower()

    def _get_widget_value(self) -> Any:
        """Get the value corresponding to current checkbox state."""
        if self._checkbox.isChecked():
            return self._checked_value
        return self._unchecked_value

    def _set_widget_value(self, value: Any) -> None:
        """Set the checkbox state based on a value."""
        self._value = value
        self._update_display()

    def _update_readonly_state(self) -> None:
        """Update checkbox enabled state based on readonly property."""
        self._checkbox.setEnabled(not self._readonly and self._connected)

    # Event handlers

    @Slot(int)
    def _on_state_changed(self, state: int) -> None:
        """Handle checkbox state change."""
        if self._updating_from_pv:
            return

        checked = state == Qt.CheckState.Checked.value
        self.toggled.emit(checked)

        if self._write_on_change and self._connected and not self._readonly:
            value = self._checked_value if checked else self._unchecked_value
            try:
                self.write_value(value)
                self.value_written.emit(value)
            except Exception:
                # Revert on error
                self._update_display()

    def _on_pv_metadata_changed(self, metadata: dict[str, Any]) -> None:
        """Handle metadata changes for enum PVs."""
        super()._on_pv_metadata_changed(metadata)

        # Use enum strings as labels if available
        enum_strings = metadata.get("enum_strings", [])
        if len(enum_strings) >= 2:
            # Use second enum string as checkbox text if not already set
            if not self._checkbox.text():
                self._checkbox.setText(enum_strings[1])

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get checkbox-specific introspection data."""
        return {
            "checked": self._checkbox.isChecked(),
            "label_text": self._checkbox.text(),
            "checked_value": self._checked_value,
            "unchecked_value": self._unchecked_value,
            "write_on_change": self._write_on_change,
            "editable": not self._readonly and self._connected,
        }
