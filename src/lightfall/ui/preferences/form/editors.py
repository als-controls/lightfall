"""Field editors with an explicit "not set" state.

Every editor maps ``None`` to "not set" and keeps real falsy values
(``False``, ``0``, ``""``) distinct from it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from qtpy.QtCore import QEvent, QObject, Qt, Signal
from qtpy.QtGui import QColor, QPalette
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


_TEXT_ROLES = (QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText, QPalette.ColorRole.Text)


_ACTIVE_GROUPS = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive)
_DISABLED_GROUP = (QPalette.ColorGroup.Disabled,)


def _theme_color(name: str) -> QColor | None:
    """A colour from the running Lightfall theme, or None if there is none."""
    try:
        from lightfall.ui.theme import ThemeManager

        color = QColor(getattr(ThemeManager.get_instance().colors, name))
    except Exception:  # noqa: BLE001 - theme unavailable (early init, tests)
        return None
    return color if color.isValid() else None


def _muted_color(widget: QWidget) -> QColor:
    """Secondary text colour for "Not set", hints and read-only values.

    Taken from the theme's ``text_secondary``: the theme builds its palette
    with every colour group equal, so palette roles cannot be trusted to
    differ from normal text. Falls back to PlaceholderText without a theme.
    """
    return _theme_color("text_secondary") or widget.palette().color(QPalette.ColorRole.PlaceholderText)


def _error_color(widget: QWidget) -> QColor:
    """Theme error colour; falls back to a palette alert role."""
    return _theme_color("error") or widget.palette().color(QPalette.ColorRole.Highlight)


class _RoleColor(QObject):
    """Keeps a colour override on a widget in step with theme changes.

    Only the given roles of the given colour groups (Active and Inactive by
    default) are overridden on a fresh palette, so everything else keeps
    inheriting from the theme. ``color_fn`` returns the colour to show, or
    None to clear.
    """

    def __init__(
        self,
        widget: QWidget,
        roles: tuple[QPalette.ColorRole, ...],
        color_fn: Callable[[QWidget], QColor | None],
        groups: tuple[QPalette.ColorGroup, ...] = _ACTIVE_GROUPS,
    ) -> None:
        super().__init__(widget)
        self._widget = widget
        self._roles = roles
        self._groups = groups
        self._color_fn = color_fn
        self._last: QColor | None = None
        widget.installEventFilter(self)
        self.refresh()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._widget and event.type() == QEvent.Type.PaletteChange:
            self.refresh()
        return False

    def refresh(self) -> None:
        color = self._color_fn(self._widget)
        if color == self._last:
            return
        self._last = color
        pal = QPalette()
        if color is not None:
            for group in self._groups:
                for role in self._roles:
                    pal.setColor(group, role, color)
        self._widget.setPalette(pal)


class _ReadOnlyLook(QObject):
    """Makes a read-only line edit look like a value, not an input box.

    The theme's palette gives disabled and enabled text the same colour and
    its stylesheet gives every QLineEdit the same box, so neither can mark
    read-only fields. Instead the box is flattened (no border, no
    background) and the text drawn in the muted colour; the edit stays
    enabled so the value can still be selected and copied.
    """

    def __init__(self, edit: QLineEdit) -> None:
        super().__init__(edit)
        self._edit = edit
        self.read_only = False
        self._last = ""
        edit.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._edit and event.type() == QEvent.Type.PaletteChange:
            self.refresh()
        return False

    def set_read_only(self, read_only: bool) -> None:
        self.read_only = read_only
        self._edit.setReadOnly(read_only)
        self.refresh()

    def refresh(self) -> None:
        sheet = ""
        if self.read_only:
            muted = _muted_color(self._edit).name()
            sheet = (
                f"QLineEdit {{ color: {muted}; background: transparent;"
                " border: 1px solid transparent; }"
            )
        if sheet != self._last:
            self._last = sheet
            self._edit.setStyleSheet(sheet)


class _SegmentStyle(QObject):
    """Scoped stylesheet that makes the checked toggle segment unmistakable.

    The checked segment is filled with the theme accent (``primary``, or the
    palette Highlight) and drawn in HighlightedText; unchecked segments stay
    flat with a thin border. Read-only (disabled) segments are muted, but the
    checked one keeps a faded accent fill so its state still reads. Colours
    come from the theme and are re-applied whenever the palette changes
    (which is how a theme switch reaches widgets).
    """

    SEGMENT = "toggleSegment"
    NOT_SET = "toggleSegmentNotSet"
    READ_ONLY_ALPHA = 0.35

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._last = ""
        host.installEventFilter(self)
        self.refresh()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._host and event.type() == QEvent.Type.PaletteChange:
            self.refresh()
        return False

    @staticmethod
    def _rgba(color: QColor, alpha: float = 1.0) -> str:
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alphaF() * alpha:.3f})"

    def stylesheet(self) -> str:
        pal = self._host.palette()
        accent = _theme_color("primary") or pal.color(QPalette.ColorRole.Highlight)
        on_accent = pal.color(QPalette.ColorRole.HighlightedText)
        text = _theme_color("text") or pal.color(QPalette.ColorRole.ButtonText)
        border = _theme_color("border") or pal.color(QPalette.ColorRole.Mid)
        muted = _muted_color(self._host)
        a, on_a, t, b, m = (self._rgba(c) for c in (accent, on_accent, text, border, muted))
        faded = self._rgba(accent, self.READ_ONLY_ALPHA)

        def sel(state: str = "") -> str:
            return f"QToolButton#{self.SEGMENT}{state}, QToolButton#{self.NOT_SET}{state}"

        return "\n".join((
            f"{sel()} {{ background: transparent; border: 1px solid {b}; padding: 2px 8px; color: {t}; }}",
            f"QToolButton#{self.NOT_SET} {{ color: {m}; }}",
            f"{sel(':checked')} {{ background: {a}; border-color: {a}; color: {on_a}; }}",
            f"{sel(':disabled')} {{ color: {m}; }}",
            f"{sel(':checked:disabled')} {{ background: {faded}; border-color: {a}; color: {t}; }}",
        ))

    def refresh(self) -> None:
        sheet = self.stylesheet()
        if sheet != self._last:
            self._last = sheet
            self._host.setStyleSheet(sheet)


class _OutOfRangeError(ValueError):
    """Parsed number lies outside the editor's bounds."""


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
            btn.setObjectName(_SegmentStyle.NOT_SET if val is None else _SegmentStyle.SEGMENT)
            self._group.addButton(btn)
            layout.addWidget(btn)
            self._buttons[val] = btn
        self._buttons[None].setChecked(True)
        self._segment_style = _SegmentStyle(self)
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
        _RoleColor(self._combo, _TEXT_ROLES, _muted_color, _DISABLED_GROUP)
        self._combo.addItem(NOT_SET)
        self._mute_not_set_entry()
        for val, label in options:
            self._values.append(val)
            self._combo.addItem(label)
        self._combo.currentIndexChanged.connect(self._on_index)

    def _mute_not_set_entry(self) -> None:
        self._combo.setItemData(0, _muted_color(self._combo), Qt.ItemDataRole.ForegroundRole)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "_combo"):
            self._mute_not_set_entry()
        super().changeEvent(event)

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

    def __init__(self, spec: EditorSpec, unit: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._integer = spec.kind == "integer"
        self._min = spec.minimum
        self._max = spec.maximum
        self._step = spec.step
        self._read_only = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(NOT_SET)
        self._edit.installEventFilter(self)
        layout.addWidget(self._edit)
        if unit:
            self._unit = QLabel(unit, self)
            _RoleColor(self._unit, _TEXT_ROLES, _muted_color)
            layout.addWidget(self._unit)
        self._value: int | float | None = None
        self._valid = True
        self._hint = QLabel(self)
        self._hint.hide()
        layout.addWidget(self._hint)
        _RoleColor(self._hint, _TEXT_ROLES, _muted_color)
        self._text_style = _RoleColor(self._edit, (QPalette.ColorRole.Text,), self._text_color)
        self._read_only_look = _ReadOnlyLook(self._edit)
        self._edit.textEdited.connect(self._on_edited)

    def _text_color(self, widget: QWidget) -> QColor | None:
        return None if self._valid else _error_color(widget)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        """Up/Down arrow keys step the number by the spec's ``step``."""
        if obj is self._edit and event.type() == QEvent.Type.KeyPress and self._step:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and not self._read_only:
                self._step_by(self._step if key == Qt.Key.Key_Up else -self._step)
                return True
        return super().eventFilter(obj, event)

    def _step_by(self, delta: float) -> None:
        if not self._valid:
            return
        if self._value is None:
            num = self._min if self._min is not None else 0
        else:
            num = self._value + delta
        if self._min is not None:
            num = max(num, self._min)
        if self._max is not None:
            num = min(num, self._max)
        num = int(round(num)) if self._integer else round(float(num), 10)
        text = str(num)
        self._edit.setText(text)
        self._on_edited(text)

    def _parse(self, text: str) -> int | float:
        """Parse ``text``; raise ValueError if unparsable or out of range."""
        num = int(text) if self._integer else float(text)
        if not math.isfinite(num):
            raise ValueError("not finite")
        if (self._min is not None and num < self._min) or (self._max is not None and num > self._max):
            raise _OutOfRangeError(str(num))
        return num

    def _on_edited(self, text: str) -> None:
        text = text.strip()
        reason = ""
        if not text:
            self._value = None
        else:
            try:
                self._value = self._parse(text)
            except _OutOfRangeError:
                reason = self._range_hint()
            except ValueError:
                reason = "Not an integer" if self._integer else "Not a number"
        self._set_valid(not reason, reason)
        self.changed.emit()

    def _set_valid(self, ok: bool, reason: str = "") -> None:
        self._valid = ok
        self._hint.setText(reason)
        self._hint.setVisible(not ok)
        self._edit.setToolTip(reason)
        self._text_style.refresh()

    def _range_hint(self) -> str:
        if self._min is not None and self._max is not None:
            return f"Out of range ({self._min:g}\u2013{self._max:g})"
        if self._min is not None:
            return f"Out of range (at least {self._min:g})"
        return f"Out of range (at most {self._max:g})"

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
        self._read_only = read_only
        self._read_only_look.set_read_only(read_only)


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
        _RoleColor(self.empty_hint, _TEXT_ROLES, _muted_color)
        layout.addWidget(self.empty_hint)
        self._value: str | None = None
        self._read_only_look = _ReadOnlyLook(self._edit)
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
        self._read_only_look.set_read_only(read_only)


def create_editor(spec: EditorSpec, field: SettingField, parent: QWidget | None = None) -> FieldEditor:
    """Build the editor widget for ``spec``; unknown kinds raise ValueError."""
    if spec.kind == "toggle":
        editor: FieldEditor = ToggleEditor()
    elif spec.kind == "choice":
        editor = ChoiceEditor(spec)
    elif spec.kind in ("integer", "number"):
        editor = NumberEditor(spec, field.unit)
    elif spec.kind == "text":
        editor = TextEditor(field.placeholder)
    else:
        raise ValueError(f"unknown editor kind: {spec.kind!r}")
    if parent is not None:
        editor.setParent(parent)
    return editor
