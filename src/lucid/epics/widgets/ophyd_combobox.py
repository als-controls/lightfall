# src/lucid/epics/widgets/ophyd_combobox.py
"""OphydComboBox — dropdown for ophyd enum signal values."""
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QComboBox, QWidget, QHBoxLayout

from lucid.epics.widgets.ophyd_base import OphydWidget


class OphydComboBox(OphydWidget):
    widget_type: ClassVar[str] = "OphydComboBox"
    widget_description: ClassVar[str] = "Dropdown for ophyd enum signal values"

    selection_changed = Signal(int, str)
    value_written = Signal(object)

    def __init__(
        self,
        signal: Any = None,
        parent: QWidget | None = None,
        write_on_change: bool = True,
        readonly: bool = False,
        show_units: bool = True,
    ) -> None:
        self._write_on_change = write_on_change
        self._updating_from_signal = False

        super().__init__(signal, parent, readonly=readonly, show_units=show_units)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._combo = QComboBox()
        self._layout.addWidget(self._combo)
        self._layout.addWidget(self._ensure_units_label())

        self._combo.currentIndexChanged.connect(self._on_index_changed)

        self._update_readonly_state()

    def set_items(self, items: list[str]) -> None:
        self._combo.clear()
        for i, text in enumerate(items):
            self._combo.addItem(text, i)

    def _update_display(self) -> None:
        if self._value is None:
            return
        self._updating_from_signal = True
        try:
            index = int(self._value)
            if 0 <= index < self._combo.count():
                self._combo.setCurrentIndex(index)
        except (ValueError, TypeError):
            text = str(self._value)
            index = self._combo.findText(text)
            if index >= 0:
                self._combo.setCurrentIndex(index)
        finally:
            self._updating_from_signal = False

    def _get_widget_value(self) -> Any:
        return self._combo.currentIndex()

    def _set_widget_value(self, value: Any) -> None:
        self._value = value
        self._update_display()

    def _update_readonly_state(self) -> None:
        self._combo.setEnabled(not self._readonly and self._connected)

    @Slot(int)
    def _on_index_changed(self, index: int) -> None:
        if self._updating_from_signal:
            return
        text = self._combo.currentText()
        self.selection_changed.emit(index, text)
        if self._write_on_change and self._connected and not self._readonly:
            try:
                self.write_value(index)
                self.value_written.emit(index)
            except Exception:
                self._update_display()
