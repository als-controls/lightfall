"""Accessible-name resolution: what a person would call a widget.

objectName is never a name; the snapshot reports it separately as a hint.
"""

from __future__ import annotations

from PySide6.QtWidgets import QAbstractButton, QFormLayout, QGroupBox, QLabel, QLineEdit, QWidget

from lightfall.ui.surface.roles import role_of

MAX_NAME = 80


def clean(text: str) -> str:
    """Drop mnemonic ampersands (keeping a literal ``&&``) and collapse whitespace."""
    text = text.replace("&&", "\0").replace("&", "").replace("\0", "&")
    return " ".join(text.split())


def _label_text(label: QLabel) -> str:
    return clean(label.text()).rstrip(":").strip()


def labelled_by(w: QWidget) -> QLabel | None:
    """The QLabel naming field *w*: its QFormLayout row label, else a buddy label.

    Only each ancestor's own layout is checked; a form nested in a sub-layout falls
    through to the buddy and tooltip rules (and the audit reports it if unnamed)."""
    child, parent = w, w.parentWidget()
    while parent is not None and not child.isWindow():
        layout = parent.layout()
        if isinstance(layout, QFormLayout):
            label = layout.labelForField(child)
            if isinstance(label, QLabel):
                return label
        child, parent = parent, parent.parentWidget()
    for label in w.window().findChildren(QLabel):
        if label.buddy() is w:
            return label
    return None


def accessible_name(w: QWidget) -> str:
    """What a person would call *w*.

    - buttons: visible text > accessibleName > tooltip
    - windows, dialogs, docks, group boxes: title > accessibleName
    - labels: text
    - everything else (fields, combos, views, sliders): accessibleName > form label >
      buddy label > placeholder > tooltip
    """
    acc = clean(w.accessibleName())
    tip = clean(w.toolTip())[:MAX_NAME]
    if isinstance(w, QAbstractButton):
        return clean(w.text()) or acc or tip
    if isinstance(w, QGroupBox):
        return clean(w.title()) or acc
    role = role_of(w)
    if role == "menu":
        return clean(w.title()) or acc
    if role in ("window", "dialog", "dock"):
        return clean(w.windowTitle()) or acc
    if isinstance(w, QLabel):
        return clean(w.text())[:MAX_NAME]
    if acc:
        return acc
    if (label := labelled_by(w)) is not None and (name := _label_text(label)):
        return name
    if isinstance(w, QLineEdit) and w.placeholderText():
        return clean(w.placeholderText())
    return tip
