# src/lightfall/epics/widgets/ophyd_spinbox.py
"""OphydSpinBox — numeric spin box for ophyd signal values."""
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QWidget

from lightfall.epics.widgets.ophyd_base import OphydWidget


class OphydSpinBox(OphydWidget):
    widget_type: ClassVar[str] = "OphydSpinBox"
    widget_description: ClassVar[str] = "Spin box for numeric ophyd signal values"

    value_written = Signal(object)

    def __init__(
        self,
        signal: Any = None,
        parent: QWidget | None = None,
        minimum: float = -1e6,
        maximum: float = 1e6,
        decimals: int = 1,
        write_on_change: bool = True,
        readonly: bool = False,
        show_units: bool = True,
    ) -> None:
        self._write_on_change = write_on_change
        self._updating_from_signal = False

        super().__init__(signal, parent, readonly=readonly, show_units=show_units)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._spinbox = QDoubleSpinBox()
        self._spinbox.setMinimum(minimum)
        self._spinbox.setMaximum(maximum)
        self._spinbox.setDecimals(decimals)
        self._layout.addWidget(self._spinbox)
        self._layout.addWidget(self._ensure_units_label())

        self._spinbox.valueChanged.connect(self._on_value_changed)

        self._update_readonly_state()

    def _update_display(self) -> None:
        if self._value is None:
            return
        self._updating_from_signal = True
        try:
            self._spinbox.setValue(float(self._value))
        except (ValueError, TypeError):
            pass
        finally:
            self._updating_from_signal = False

    def _get_widget_value(self) -> float:
        return self._spinbox.value()

    def _set_widget_value(self, value: Any) -> None:
        self._value = value
        self._update_display()

    def _update_readonly_state(self) -> None:
        self._spinbox.setReadOnly(self._readonly or not self._connected)

    @Slot(float)
    def _on_value_changed(self, value: float) -> None:
        if self._updating_from_signal:
            return
        if self._write_on_change and self._connected and not self._readonly:
            try:
                self.write_value(value)
                self.value_written.emit(value)
            except Exception:
                self._update_display()
