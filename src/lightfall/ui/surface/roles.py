"""Widget -> role, and the protocol custom widgets implement to describe themselves.

A role is what a person (or a screen reader) would call a widget: a button, a field,
a list. Plain containers have no role; the snapshot flattens them.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
    QTabBar,
    QTableView,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QTreeView,
    QWidget,
)


@runtime_checkable
class AgentDescribable(Protocol):
    """Optional protocol for custom widgets (plots, images, synoptics).

    ``agent_describe()`` returns a small JSON-able dict shown after the node's name,
    e.g. ``{"series": [...], "x_label": "sample_lift"}``. Include ``"opaque": True`` to
    stop the snapshot descending into the widget's children.
    """

    def agent_role(self) -> str: ...

    def agent_describe(self) -> dict[str, Any]: ...


#: Roles kept as containers in the outline (their children nest under them).
LANDMARKS = frozenset({"window", "dialog", "dock", "tabs", "tab", "group", "menubar", "toolbar",
                       "menu"})

#: Roles a person can operate (the audit requires these to have names).
INTERACTIVE = frozenset({"button", "toggle", "checkbox", "radio", "combo", "field", "list",
                         "table", "tree", "slider", "tab", "item"})

#: Roles whose child widgets are implementation detail (an editor inside a spin box).
LEAVES = frozenset({"button", "toggle", "checkbox", "radio", "combo", "field", "slider",
                    "progress", "label"})


def is_describable(w: QWidget) -> bool:
    return callable(getattr(w, "agent_role", None)) and callable(getattr(w, "agent_describe", None))


def role_of(w: QWidget) -> str | None:
    """The role of *w*, or None for a plain container the snapshot flattens."""
    if is_describable(w):
        return w.agent_role()
    if isinstance(w, QDialog):
        return "dialog"
    if isinstance(w, QMainWindow):
        return "window"
    if isinstance(w, QDockWidget):
        return "dock"
    if isinstance(w, QMenu):
        return "menu"
    if isinstance(w, QMenuBar):
        return "menubar"
    if isinstance(w, QToolBar):
        return "toolbar"
    if isinstance(w, QTabWidget):
        return "tabs"
    if isinstance(w, QTabBar):
        return None  # rendered as tab items of the QTabWidget
    if isinstance(w, QGroupBox):
        return "group"
    if isinstance(w, QCheckBox):
        return "checkbox"
    if isinstance(w, QRadioButton):
        return "radio"
    if isinstance(w, QAbstractButton):
        return "toggle" if w.isCheckable() else "button"
    if isinstance(w, QComboBox):
        return "combo"
    if isinstance(w, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)):
        return "field"
    if isinstance(w, QTableView):
        return "table"
    if isinstance(w, QTreeView):
        return "tree"
    if isinstance(w, QAbstractItemView):
        return "list"
    if isinstance(w, QAbstractSlider):
        return "slider"
    if isinstance(w, QProgressBar):
        return "progress"
    if isinstance(w, QLabel):
        return "label"
    if w.isWindow():
        return "window"
    return None
