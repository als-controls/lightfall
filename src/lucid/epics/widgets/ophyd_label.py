"""OphydLabel — read-only display for ophyd signal values."""
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtWidgets import QLabel, QWidget, QHBoxLayout

from lucid.epics.widgets.ophyd_base import OphydWidget


class OphydLabel(OphydWidget):
    widget_type: ClassVar[str] = "OphydLabel"
    widget_description: ClassVar[str] = "Read-only label for ophyd signal values"

    def __init__(
        self,
        signal: Any = None,
        parent: QWidget | None = None,
        precision: int = 4,
    ) -> None:
        self._precision = precision
        super().__init__(signal, parent, readonly=True)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._value_label = QLabel("---")
        self._layout.addWidget(self._value_label)

    def _update_display(self) -> None:
        if self._value is None:
            self._value_label.setText("---")
            return
        self._value_label.setText(self._format_value(self._value))

    def _format_value(self, value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.{self._precision}f}"
        return str(value)

    def _get_widget_value(self) -> Any:
        return self._value

    def _set_widget_value(self, value: Any) -> None:
        self._value = value
        self._update_display()
