"""Widgets for form items that are not preferences: actions and status lines."""

from __future__ import annotations

from collections.abc import Callable

import shiboken6
from qtpy.QtGui import QColor
from qtpy.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from lightfall.ui.preferences.form.editors import TEXT_ROLES, RoleColor, error_color, muted_color
from lightfall.ui.preferences.form.row import LABEL_INDENT, ROW_MARGIN_V, TEXT_INSET
from lightfall.ui.preferences.form.schema import Action, FormValues, Info
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

WORKING = "Working…"


class ActionItem(QWidget):
    """A push button with a status label beside it.

    A click disables the button, shows "Working..." and runs ``action.run``
    on a ``QThreadFuture`` with the values from ``snapshot()`` (taken on the
    GUI thread). The returned text, or the exception's text in the error
    colour, then replaces the status. A result arriving after the widget is
    gone is dropped.
    """

    def __init__(self, action: Action, snapshot: Callable[[], FormValues], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.action = action
        self._snapshot = snapshot
        self._enabled_by_predicate = True
        self._state = "idle"  # idle | working | done | failed
        self.future: QThreadFuture | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        self.button = QPushButton(action.label, self)
        self.button.clicked.connect(self._start)
        layout.addWidget(self.button)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status, 1)
        self._status_color = RoleColor(self.status, TEXT_ROLES, self._color_for_state)

    def set_predicate_enabled(self, enabled: bool) -> None:
        """Apply ``enabled_when``; a running action stays disabled until it finishes."""
        self._enabled_by_predicate = enabled
        self.button.setEnabled(enabled and self._state != "working")

    def _color_for_state(self, widget: QWidget) -> QColor | None:
        if self._state == "working":
            return muted_color(widget)
        if self._state == "failed":
            return error_color(widget)
        return None

    def _set_state(self, state: str, text: str) -> None:
        self._state = state
        self.status.setText(text)
        self._status_color.refresh()
        self.button.setEnabled(self._enabled_by_predicate and state != "working")

    def _start(self) -> None:
        if self._state == "working":
            return
        values = self._snapshot()
        self._set_state("working", WORKING)
        self.future = QThreadFuture(
            self.action.run,
            values,
            callback_slot=lambda text: self._finish("done", "" if text is None else str(text)),
            except_slot=lambda exc: self._finish("failed", str(exc)),
            name="preferences-action",
            log_exceptions=False,
        )
        self.future.start()

    def _finish(self, state: str, text: str) -> None:
        if not shiboken6.isValid(self):
            logger.debug("Preferences action '{}' finished after its page closed; result dropped", self.action.label)
            return
        self._set_state(state, text)


class InfoItem(QLabel):
    """A muted, word-wrapped status line; ``update_text`` re-evaluates it.

    An empty text hides the line (so a callable can show it only when relevant).

    A text callable that raises shows the exception text (still muted) and
    is logged; it never breaks the rest of the form.
    """

    def __init__(self, info: Info, section: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self._section = section
        self.setWordWrap(True)
        RoleColor(self, TEXT_ROLES, muted_color)
        if info.field:  # about one field: line up with the row labels, spaced like a row
            # QLabel margin/indent, not contentsMargins: style-sheet polish resets the latter.
            self.setMargin(ROW_MARGIN_V + TEXT_INSET)
            self.setIndent(LABEL_INDENT - ROW_MARGIN_V - TEXT_INSET)

    def update_text(self, values: FormValues) -> None:
        if not callable(self.info.text):
            self._show(self.info.text)
            return
        try:
            text = self.info.text(values)
        except Exception as exc:  # noqa: BLE001 - a page bug must not break the form
            name = getattr(self.info.text, "__qualname__", repr(self.info.text))
            logger.warning("Info line {} in section '{}' failed: {!r}", name, self._section, exc)
            text = str(exc)
        self._show(text)

    def _show(self, text: str) -> None:
        self.setText(text)
        self.setVisible(bool(text))
