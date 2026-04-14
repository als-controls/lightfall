"""OphydLineEdit — text input for ophyd signal values."""
from __future__ import annotations

import inspect
from typing import Any, ClassVar

from PySide6.QtCore import Property, Signal, Slot
from PySide6.QtWidgets import QLineEdit, QWidget, QHBoxLayout, QLabel

from lucid.epics.widgets.ophyd_base import OphydWidget
from lucid.epics.widgets.style import WidgetStyles


class OphydLineEdit(OphydWidget):
    widget_type: ClassVar[str] = "OphydLineEdit"
    widget_description: ClassVar[str] = "Text input for ophyd signal values"

    editing_finished = Signal()
    value_written = Signal(object)

    def __init__(
        self,
        signal: Any = None,
        parent: QWidget | None = None,
        precision: int = 4,
        show_units: bool = True,
        write_on_enter: bool = True,
        write_on_focus_out: bool = False,
        readonly: bool = False,
    ) -> None:
        self._precision = precision
        self._show_units = show_units
        self._units: str = ""
        self._write_on_enter = write_on_enter
        self._write_on_focus_out = write_on_focus_out
        self._modified = False

        super().__init__(signal, parent, readonly=readonly)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._line_edit = QLineEdit()
        self._layout.addWidget(self._line_edit)

        self._units_label = QLabel()
        self._layout.addWidget(self._units_label)
        self._units_label.setVisible(False)

        self._line_edit.textChanged.connect(self._on_text_changed)
        self._line_edit.returnPressed.connect(self._on_return_pressed)
        self._line_edit.editingFinished.connect(self._on_editing_finished)

        self._update_readonly_state()

    @Property(bool)
    def show_units(self) -> bool:
        return self._show_units

    @show_units.setter
    def show_units(self, value: bool) -> None:
        self._show_units = value
        self._units_label.setVisible(value and bool(self._units))

    @Property(int)
    def precision(self) -> int:
        return self._precision

    @precision.setter
    def precision(self, value: int) -> None:
        self._precision = value
        self._update_display()

    @Slot()
    def _apply_value_update(self) -> None:
        if self._modified and self._value is not None:
            try:
                if self._get_widget_value() == self._value:
                    self._modified = False
                    self._update_modified_style()
            except (ValueError, TypeError):
                pass
        super()._apply_value_update()

    def _update_display(self) -> None:
        if self._modified:
            return
        if self._value is None:
            self._line_edit.setText("")
            return
        text = self._format_value(self._value)
        self._line_edit.blockSignals(True)
        self._line_edit.setText(text)
        self._line_edit.blockSignals(False)

    def text(self) -> str:
        """Return the current text from the inner QLineEdit."""
        return self._line_edit.text()

    def _get_widget_value(self) -> Any:
        text = self._line_edit.text().strip()
        if self._value is not None:
            try:
                # Check int before float (int is a subclass of numbers but
                # we want to preserve the distinction)
                if isinstance(self._value, int) and not isinstance(self._value, bool):
                    return int(text)
                elif isinstance(self._value, float):
                    return float(text)
                else:
                    # Try numeric conversion for numpy-like types
                    float(self._value)  # test if original was numeric
                    return float(text)
            except (ValueError, TypeError):
                pass
        return text

    def _set_widget_value(self, value: Any) -> None:
        self._value = value
        self._modified = False
        self._update_display()

    def _format_value(self, value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.{self._precision}f}"
        if isinstance(value, int):
            return str(value)
        # Fallback: try numeric conversion (catches residual numpy types)
        try:
            return f"{float(value):.{self._precision}f}"
        except (ValueError, TypeError):
            return str(value)

    def _update_readonly_state(self) -> None:
        self._line_edit.setReadOnly(self._readonly or not self._connected)

    @Slot()
    def _on_text_changed(self) -> None:
        self._modified = True
        self._update_modified_style()

    @Slot()
    def _on_return_pressed(self) -> None:
        if self._write_on_enter and self._modified:
            self._write_current_value()

    @Slot()
    def _on_editing_finished(self) -> None:
        self.editing_finished.emit()
        if self._write_on_focus_out and self._modified:
            self._write_current_value()

    def _write_current_value(self) -> None:
        if not self._connected or self._readonly:
            return
        try:
            value = self._get_widget_value()
            self.write_value(value)
            self._modified = False
            self._update_modified_style()
            self.value_written.emit(value)
        except Exception:
            self._modified = False
            self._update_display()

    def _connect_signal(self) -> None:
        super()._connect_signal()
        self._fetch_units()

    def _fetch_units(self) -> None:
        """Extract units from the ophyd signal metadata."""
        if self._signal is None:
            return
        try:
            if hasattr(self._signal, "metadata") and isinstance(
                self._signal.metadata, dict
            ):
                self._units = self._signal.metadata.get("units", "")
            elif hasattr(self._signal, "describe"):
                desc = self._signal.describe()
                if inspect.isawaitable(desc):
                    return
                if desc:
                    for _key, info in desc.items():
                        if isinstance(info, dict):
                            self._units = info.get("units", "")
                            break
        except Exception:
            pass
        self._units_label.setText(self._units)
        self._units_label.setVisible(self._show_units and bool(self._units))

    def _update_modified_style(self) -> None:
        if self._modified:
            self._line_edit.setStyleSheet(WidgetStyles.modified())
        else:
            self._line_edit.setStyleSheet("")

    def _get_specific_introspection_data(self) -> dict[str, Any]:
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
