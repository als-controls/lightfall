"""ScopeHeader: the scope selector at the top of the Preferences dialog's page pane."""

from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from lightfall.ui.preferences.form.row import set_muted
from lightfall.ui.preferences.scope_api import ScopeChoice

LEGACY_TEXT = "This page saves to your user settings only"
DIRTY_MARK = " •"


class ScopeHeader(QWidget):
    """Combo of scope choices, a state text and a dot on scopes with unsaved edits.

    ``scope_selected`` fires only for user picks; ``set_choices`` and
    ``set_selected`` change the combo silently.
    """

    scope_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._choices: list[ScopeChoice] = []
        self._dirty: set[str] = set()
        self._aware = True
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Scope:", self))
        self._combo = QComboBox(self)
        self._combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._combo.currentIndexChanged.connect(self._on_index)
        layout.addWidget(self._combo)
        self._state = QLabel(self)
        self._state.setWordWrap(True)
        set_muted(self._state)
        layout.addWidget(self._state, 1)
        # Takes the slack while the state text is hidden, so the combo stays put.
        layout.addStretch(0)

    # Public API -----------------------------------------------------------
    def set_choices(self, choices: list[ScopeChoice], selected: str) -> None:
        self._choices = list(choices)
        self._combo.blockSignals(True)
        try:
            self._combo.clear()
            for choice in self._choices:
                self._combo.addItem(self._item_text(choice), choice.scope)
        finally:
            self._combo.blockSignals(False)
        self.set_selected(selected)

    def set_selected(self, scope: str) -> None:
        """Select ``scope`` in the combo without emitting ``scope_selected``."""
        index = self._combo.findData(scope)
        if index < 0:
            return
        self._combo.blockSignals(True)
        try:
            self._combo.setCurrentIndex(index)
        finally:
            self._combo.blockSignals(False)
        self._refresh_state()

    def set_dirty_scopes(self, scopes: set[str]) -> None:
        self._dirty = set(scopes)
        for i, choice in enumerate(self._choices):
            self._combo.setItemText(i, self._item_text(choice))

    def set_page_scope_aware(self, aware: bool) -> None:
        self._aware = aware
        self._refresh_state()

    def selected(self) -> str:
        return self._combo.currentData() or ""

    # Internals ------------------------------------------------------------
    def _item_text(self, choice: ScopeChoice) -> str:
        return choice.label + (DIRTY_MARK if choice.scope in self._dirty else "")

    def _on_index(self, _index: int) -> None:
        self._refresh_state()
        scope = self.selected()
        if scope:
            self.scope_selected.emit(scope)

    def _refresh_state(self) -> None:
        # A single choice (scopes off) leaves nothing to pick.
        self._combo.setEnabled(self._aware and len(self._choices) > 1)
        if not self._aware:
            text = LEGACY_TEXT
        else:
            choice = next((c for c in self._choices if c.scope == self.selected()), None)
            text = choice.read_only_reason if choice is not None else ""
        self._state.setText(text)
        self._state.setVisible(bool(text))
