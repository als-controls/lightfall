# src/lightfall/epics/widgets/ophyd_combobox.py
"""OphydComboBox — dropdown for ophyd enum signal values."""
from __future__ import annotations

import inspect
from typing import Any, ClassVar

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from lightfall.epics.widgets.ophyd_base import OphydWidget


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

    # -- Dynamic enum population ----------------------------------------------

    def _fetch_enum_strs(self) -> list[str]:
        """Read the enum strings the bound signal actually advertises.

        For EPICS-backed signals this comes from the PV's ``enum_strs``
        (populated once the channel's control info arrives). Falls back to
        ``describe()`` metadata. Returns an empty list for soft signals or
        non-enum PVs, in which case any items set via ``set_items()`` are
        left untouched.
        """
        sig = self._signal
        if sig is None:
            return []

        try:
            enum_strs = getattr(sig, "enum_strs", None)
            if enum_strs:
                return [str(s) for s in enum_strs]
        except Exception:
            pass

        try:
            if hasattr(sig, "describe"):
                desc = sig.describe()
                if inspect.isawaitable(desc):
                    return []
                if desc:
                    for info in desc.values():
                        if isinstance(info, dict) and info.get("enum_strs"):
                            return [str(s) for s in info["enum_strs"]]
        except Exception:
            pass

        return []

    def _populate_enum_items(self) -> bool:
        """Replace the dropdown items with the signal's enum strings.

        Returns True if the items changed. Repopulation is suppressed from
        triggering a write-back: clearing/adding items emits
        ``currentIndexChanged``, which would otherwise put a spurious value
        to the signal.
        """
        enum_strs = self._fetch_enum_strs()
        if not enum_strs:
            return False

        current = [self._combo.itemText(i) for i in range(self._combo.count())]
        if enum_strs == current:
            return False

        self._updating_from_signal = True
        try:
            self.set_items(enum_strs)
        finally:
            self._updating_from_signal = False
        return True

    def _connect_signal(self) -> None:
        # Base class subscribes, reads the initial value, and calls
        # _update_display() before we have enum items. Populate from the
        # signal's enum_strs, then re-apply the value so the right index
        # is selected.
        super()._connect_signal()
        if self._populate_enum_items():
            self._update_display()

    @Slot()
    def _apply_value_update(self) -> None:
        # enum_strs may only become available after the PV connects; if we
        # still have no items when the first value arrives, populate now.
        if self._combo.count() == 0:
            self._populate_enum_items()
        super()._apply_value_update()

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
