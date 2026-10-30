"""Field editors with an explicit "not set" state.

Every editor maps ``None`` to "not set" and keeps real falsy values
(``False``, ``0``, ``""``) distinct from it.
"""

from __future__ import annotations

from typing import Any

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QPalette
from qtpy.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QWidget,
)

from lightfall.ui.preferences.form.schema import EditorSpec, SettingField

NOT_SET = "Not set"


def _muted(widget: QWidget) -> QPalette:
    """Copy of the widget's palette with text roles set to the muted colour."""
    pal = QPalette(widget.palette())
    muted = pal.color(QPalette.ColorRole.PlaceholderText)
    for role in (QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
        pal.setColor(role, muted)
    return pal


class FieldEditor(QWidget):
    """Base class: ``value()`` is ``None`` when the setting is not set."""

    changed = Signal()

    def value(self) -> Any:
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        """Show ``value`` (None = not set) without emitting ``changed``."""
        raise NotImplementedError

    def set_read_only(self, read_only: bool) -> None:
        raise NotImplementedError

    def is_valid(self) -> bool:
        """False while the user's input cannot be parsed or is out of range."""
        return True


class ToggleEditor(FieldEditor):
    """Segmented control: Not set / Off / On."""

    _SEGMENTS = ((NOT_SET, None), ("Off", False), ("On", True))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[Any, QToolButton] = {}
        self._loading = False
        for text, val in self._SEGMENTS:
            btn = QToolButton(self)
            btn.setText(text)
            btn.setCheckable(True)
            if val is None:
                btn.setPalette(_muted(btn))
            self._group.addButton(btn)
            layout.addWidget(btn)
            self._buttons[val] = btn
        self._buttons[None].setChecked(True)
        self._group.buttonToggled.connect(self._on_toggled)

    def _on_toggled(self, _btn: QAbstractButton, checked: bool) -> None:
        if checked and not self._loading:
            self.changed.emit()

    def value(self) -> bool | None:
        for val, btn in self._buttons.items():
            if btn.isChecked():
                return val
        return None

    def set_value(self, value: Any) -> None:
        key = None if value is None else bool(value)
        self._loading = True
        try:
            self._buttons[key].setChecked(True)
        finally:
            self._loading = False

    def set_read_only(self, read_only: bool) -> None:
        for btn in self._buttons.values():
            btn.setEnabled(not read_only)


class ChoiceEditor(FieldEditor):
    """Combo box whose first entry is a muted "Not set"."""

    def __init__(self, spec: EditorSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._combo = QComboBox(self)
        layout.addWidget(self._combo)
        options = spec.options() if callable(spec.options) else spec.options
        self._values: list[Any] = [None]
        self._loading = False
        self._combo.addItem(NOT_SET)
        self._combo.setItemData(
            0, self.palette().color(QPalette.ColorRole.PlaceholderText), Qt.ItemDataRole.ForegroundRole
        )
        for val, label in options:
            self._values.append(val)
            self._combo.addItem(label)
        self._combo.currentIndexChanged.connect(self._on_index)

    def _on_index(self, _index: int) -> None:
        if not self._loading:
            self.changed.emit()

    def value(self) -> Any:
        return self._values[self._combo.currentIndex()]

    def set_value(self, value: Any) -> None:
        self._loading = True
        try:
            if value is None:
                index = 0
            else:
                index = next((i for i, v in enumerate(self._values) if i and v == value), -1)
                if index < 0:  # keep a stored value the options no longer list
                    self._values.append(value)
                    self._combo.addItem(str(value))
                    index = len(self._values) - 1
            self._combo.setCurrentIndex(index)
        finally:
            self._loading = False

    def set_read_only(self, read_only: bool) -> None:
        self._combo.setEnabled(not read_only)


class NumberEditor(FieldEditor):
    """Line edit for integer or number; empty means not set."""

    def __init__(self, spec: EditorSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._integer = spec.kind == "integer"
        self._min = spec.minimum
        self._max = spec.maximum
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(NOT_SET)
        layout.addWidget(self._edit)
        self._value: int | float | None = None
        self._valid = True
        self._normal_palette = QPalette(self._edit.palette())
        self._edit.textEdited.connect(self._on_edited)

    def _parse(self, text: str) -> int | float:
        """Parse ``text``; raise ValueError if unparsable or out of range."""
        num = int(text) if self._integer else float(text)
        if (self._min is not None and num < self._min) or (self._max is not None and num > self._max):
            raise ValueError("out of range")
        return num

    def _on_edited(self, text: str) -> None:
        text = text.strip()
        ok = True
        if not text:
            self._value = None
        else:
            try:
                self._value = self._parse(text)
            except ValueError:
                ok = False  # keep the last valid value
        self._set_valid(ok)
        self.changed.emit()

    def _set_valid(self, ok: bool) -> None:
        self._valid = ok
        pal = QPalette(self._normal_palette)
        if not ok:
            pal.setColor(QPalette.ColorRole.Text, pal.color(QPalette.ColorRole.BrightText))
        self._edit.setPalette(pal)
        self._edit.setToolTip("" if ok else self._range_hint())

    def _range_hint(self) -> str:
        kind = "an integer" if self._integer else "a number"
        if self._min is not None and self._max is not None:
            return f"Enter {kind} from {self._min:g} to {self._max:g}"
        if self._min is not None:
            return f"Enter {kind} of at least {self._min:g}"
        if self._max is not None:
            return f"Enter {kind} of at most {self._max:g}"
        return f"Enter {kind}"

    def value(self) -> int | float | None:
        return self._value

    def is_valid(self) -> bool:
        return self._valid

    def set_value(self, value: Any) -> None:
        if value is None:
            self._value = None
            text = ""
        else:
            self._value = int(value) if self._integer else float(value)
            text = str(self._value)
        self._edit.setText(text)
        self._set_valid(True)

    def set_read_only(self, read_only: bool) -> None:
        self._edit.setReadOnly(read_only)


class TextEditor(FieldEditor):
    """Line edit; ``""`` is a value, shown with an "(empty)" hint."""

    def __init__(self, placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._placeholder = placeholder or NOT_SET
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(self)
        layout.addWidget(self._edit)
        self.empty_hint = QLabel("(empty)", self)
        self.empty_hint.setPalette(_muted(self.empty_hint))
        layout.addWidget(self.empty_hint)
        self._value: str | None = None
        self._refresh()
        self._edit.textEdited.connect(self._on_edited)

    def _refresh(self) -> None:
        is_empty_value = self._value == ""
        self._edit.setPlaceholderText("" if is_empty_value else self._placeholder)
        self.empty_hint.setVisible(is_empty_value)

    def _on_edited(self, text: str) -> None:
        self._value = text
        self._refresh()
        self.changed.emit()

    def value(self) -> str | None:
        return self._value

    def set_value(self, value: Any) -> None:
        self._value = None if value is None else str(value)
        self._edit.setText(self._value or "")
        self._refresh()

    def set_read_only(self, read_only: bool) -> None:
        self._edit.setReadOnly(read_only)


def create_editor(spec: EditorSpec, field: SettingField, parent: QWidget | None = None) -> FieldEditor:
    """Build the editor widget for ``spec``; unknown kinds raise ValueError."""
    if spec.kind == "toggle":
        editor: FieldEditor = ToggleEditor()
    elif spec.kind == "choice":
        editor = ChoiceEditor(spec)
    elif spec.kind in ("integer", "number"):
        editor = NumberEditor(spec)
    elif spec.kind == "text":
        editor = TextEditor(field.placeholder)
    else:
        raise ValueError(f"unknown editor kind: {spec.kind!r}")
    if parent is not None:
        editor.setParent(parent)
    return editor
