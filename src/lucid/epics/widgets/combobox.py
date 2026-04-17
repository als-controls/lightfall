"""
PVComboBox widget - dropdown selection for enum PVs.

Automatically populates choices from PV enum strings.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Property, Signal, Slot
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from lucid.epics.widgets.base import EpicsWidget


class PVComboBox(EpicsWidget):
    """
    A combo box widget for selecting EPICS enum PV values.

    Features:
    - Auto-populates dropdown from PV enum strings
    - Manual items can be added for non-enum PVs
    - Selection changes can auto-write to PV
    - Shows current PV value as selected item

    Attributes:
        write_on_change: Whether to write to PV when selection changes.

    Signals:
        selection_changed: Emitted when user changes selection.
        value_written: Emitted after value is written to PV.

    Example:
        >>> combo = PVComboBox("MY:ENUM:PV")
        >>> combo.write_on_change = True
    """

    widget_type: ClassVar[str] = "PVComboBox"
    widget_description: ClassVar[str] = "Dropdown for enum PV selection"

    selection_changed = Signal(int, str)  # index, text
    value_written = Signal(object)

    def __init__(
        self,
        pv_name: str = "",
        parent: QWidget | None = None,
        write_on_change: bool = True,
    ) -> None:
        """
        Initialize the PV combo box.

        Args:
            pv_name: The EPICS PV name to connect to.
            parent: Optional Qt parent widget.
            write_on_change: Write to PV when selection changes.
        """
        self._write_on_change = write_on_change
        self._enum_strings: list[str] = []
        self._updating_from_pv = False

        super().__init__(pv_name, parent, readonly=False)

        # Create layout and combo box
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._combo = QComboBox()
        self._combo.setObjectName("combo_box")
        self._layout.addWidget(self._combo)

        # Connect signals
        self._combo.currentIndexChanged.connect(self._on_index_changed)

    # Properties

    @Property(bool)
    def write_on_change(self) -> bool:
        """Whether to write to PV when selection changes."""
        return self._write_on_change

    @write_on_change.setter
    def write_on_change(self, value: bool) -> None:
        self._write_on_change = value

    # Public methods

    def add_item(self, text: str, value: Any = None) -> None:
        """
        Add an item to the combo box.

        Args:
            text: Display text for the item.
            value: Optional value associated with the item.
        """
        if value is None:
            value = self._combo.count()
        self._combo.addItem(text, value)

    def clear_items(self) -> None:
        """Remove all items from the combo box."""
        self._combo.clear()

    def set_items(self, items: list[str]) -> None:
        """
        Set all items in the combo box.

        Args:
            items: List of display strings.
        """
        self._combo.clear()
        for i, text in enumerate(items):
            self._combo.addItem(text, i)

    # Implementation of abstract methods

    def _update_display(self) -> None:
        """Update the combo box selection from the current PV value."""
        if self._value is None:
            return

        self._updating_from_pv = True
        try:
            # Value should be an index for enum PVs
            index = int(self._value)
            if 0 <= index < self._combo.count():
                self._combo.setCurrentIndex(index)
        except (ValueError, TypeError):
            # Try to match by text
            text = str(self._value)
            index = self._combo.findText(text)
            if index >= 0:
                self._combo.setCurrentIndex(index)
        finally:
            self._updating_from_pv = False

    def _get_widget_value(self) -> Any:
        """Get the current selection index."""
        return self._combo.currentIndex()

    def _set_widget_value(self, value: Any) -> None:
        """Set the combo box selection."""
        self._value = value
        self._update_display()

    def _update_readonly_state(self) -> None:
        """Update combo box enabled state based on readonly property."""
        self._combo.setEnabled(not self._readonly and self._connected)

    # Event handlers

    @Slot(int)
    def _on_index_changed(self, index: int) -> None:
        """Handle selection change."""
        if self._updating_from_pv:
            return

        text = self._combo.currentText()
        self.selection_changed.emit(index, text)

        if self._write_on_change and self._connected and not self._readonly:
            try:
                self.write_value(index)
                self.value_written.emit(index)
            except Exception:
                # Revert on error
                self._update_display()

    def _on_pv_metadata_changed(self, metadata: dict[str, Any]) -> None:
        """Handle metadata changes to populate enum strings."""
        super()._on_pv_metadata_changed(metadata)

        enum_strings = metadata.get("enum_strings", [])
        if enum_strings and enum_strings != self._enum_strings:
            self._enum_strings = enum_strings
            self._combo.clear()
            for i, text in enumerate(enum_strings):
                self._combo.addItem(text, i)

            # Update display with current value
            self._update_display()

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get combo box-specific introspection data."""
        items = []
        for i in range(self._combo.count()):
            items.append({
                "index": i,
                "text": self._combo.itemText(i),
                "data": self._combo.itemData(i),
            })

        return {
            "write_on_change": self._write_on_change,
            "enum_strings": self._enum_strings,
            "current_index": self._combo.currentIndex(),
            "current_text": self._combo.currentText(),
            "item_count": self._combo.count(),
            "items": items,
            "editable": not self._readonly and self._connected,
        }
