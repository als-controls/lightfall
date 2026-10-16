"""Read-only Preferences page for scoped settings (phase 4a).

Shows the scope chain this machine resolves against, the user's role on each scope,
and every known setting key with its effective value and origin. Editing arrives in
phase 5; this page is also the end-to-end check of the deployed logbook server.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import shiboken6
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.scopes import scopes_available
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

if TYPE_CHECKING:
    from lightfall.scopes.view import ScopesView


def _now() -> float:
    return time.time()


def format_value(value: Any) -> str:
    return "—" if value is None else repr(value)


def _age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "just now"
    if minutes < 120:
        return f"{minutes} min ago"
    return f"{minutes // 60} h ago"


def _default_loader(bind: str) -> ScopesView:
    from lightfall.scopes.cache import SnapshotCache
    from lightfall.scopes.client import ScopesClient
    from lightfall.scopes.site import default_cache_dir
    from lightfall.scopes.view import load_view

    return load_view(ScopesClient(), SnapshotCache(default_cache_dir()), bind)


class ScopesSettingsPlugin(SettingsPlugin):
    """Read-only view of facility / beamline / branch / user settings."""

    def __init__(self) -> None:
        self.loader: Callable[[str], ScopesView] = _default_loader
        self._widget: QWidget | None = None
        self._status: QLabel | None = None
        self._bind_combo: QComboBox | None = None
        self._chain_table: QTableWidget | None = None
        self._keys_table: QTableWidget | None = None
        self._keys_note: QLabel | None = None
        self._machine_bind = "facility"
        self._future: QThreadFuture | None = None

    @property
    def name(self) -> str:
        return "scopes"

    @property
    def display_name(self) -> str:
        return "Scopes"

    @property
    def category(self) -> str:
        return "advanced"

    @property
    def priority(self) -> int:
        return 90

    def load_settings(self) -> None:
        pass

    def save_settings(self) -> None:
        pass

    # ── widget ──────────────────────────────────────────────────────────

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self._status = QLabel(widget)
        self._status.setObjectName("scopesStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._widget = widget
        widget.destroyed.connect(self._on_widget_destroyed)

        if not scopes_available():
            self._status.setText(
                "Scoped settings unavailable: install the lightfall-settings package.")
            return widget

        from lightfall.scopes.site import get_site_bind

        bind = get_site_bind()
        self._machine_bind = bind

        row = QHBoxLayout()
        row.addWidget(QLabel("This machine:", widget))
        row.addWidget(QLabel(bind if bind != "facility" else "not bound (workstation)", widget))
        row.addStretch(1)
        row.addWidget(QLabel("Preview chain for:", widget))
        self._bind_combo = QComboBox(widget)
        self._bind_combo.setObjectName("scopesBind")
        self._bind_combo.addItem(bind)
        self._bind_combo.currentTextChanged.connect(self._on_bind_chosen)
        row.addWidget(self._bind_combo)
        refresh = QPushButton("Refresh", widget)
        refresh.clicked.connect(lambda: self._refresh(self._current_bind()))
        row.addWidget(refresh)
        layout.addLayout(row)

        layout.addWidget(QLabel("<b>Scopes</b>", widget))
        self._chain_table = self._table(widget, "scopesChain", ["Scope", "Your role"])
        layout.addWidget(self._chain_table)

        layout.addWidget(QLabel("<b>Setting keys</b>", widget))
        self._keys_table = self._table(
            widget, "scopesKeys", ["Key", "Status", "Levels", "Effective value", "From", "Locked"])
        layout.addWidget(self._keys_table, 1)
        self._keys_note = QLabel(
            "Only core keys shown — the full key list needs a maintainer role.", widget)
        self._keys_note.setObjectName("scopesKeysNote")
        self._keys_note.hide()
        layout.addWidget(self._keys_note)

        self._refresh(bind)
        return widget

    @staticmethod
    def _table(parent: QWidget, name: str, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers), parent)
        t.setObjectName(name)
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def _on_widget_destroyed(self, *_: Any) -> None:
        self._widget = self._status = self._bind_combo = None
        self._chain_table = self._keys_table = self._keys_note = None

    def _alive(self) -> bool:
        return self._widget is not None and shiboken6.isValid(self._widget)

    def _current_bind(self) -> str:
        return self._bind_combo.currentText() if self._bind_combo else self._machine_bind

    # ── loading ─────────────────────────────────────────────────────────

    def _on_bind_chosen(self, bind: str) -> None:
        if bind:
            self._refresh(bind)

    def _refresh(self, bind: str) -> None:
        if self._status is not None:
            self._status.setText("Loading…")
        self._future = QThreadFuture(
            self.loader, bind,
            callback_slot=self._on_view, except_slot=self._on_error,
            name="scopes-page-load", log_exceptions=False,
        )
        self._future.start()

    def _on_error(self, exc: Exception) -> None:
        if not self._alive() or self._status is None:
            return
        status = getattr(exc, "status", None)
        if status == 401:
            self._status.setText("Log in to see scoped settings")
        elif status == 403:
            self._status.setText(f"Not allowed: {exc}")
        else:
            self._status.setText(str(exc))
        logger.info("Scopes page load failed: {}", exc)

    def _on_view(self, view: ScopesView) -> None:
        if not self._alive():
            return
        assert self._status and self._chain_table and self._keys_table and self._keys_note
        if view.offline and view.cached_at is not None:
            self._status.setText(f"Offline — cached {_age(_now() - view.cached_at)}")
        else:
            from lightfall.logbook.url import get_logbook_base_url

            self._status.setText(f"Live from {get_logbook_base_url()}")
        self._fill_combo(view)
        self._fill_chain(view)
        self._fill_keys(view)
        self._keys_note.setVisible(view.keys_restricted)

    def _fill_combo(self, view: ScopesView) -> None:
        combo = self._bind_combo
        if combo is None:
            return
        wanted = [self._machine_bind] + [s for s in view.entitled if s != self._machine_bind]
        if [combo.itemText(i) for i in range(combo.count())] == wanted:
            return
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(wanted)
        combo.setCurrentIndex(max(0, combo.findText(current)))
        combo.blockSignals(False)

    def _fill_chain(self, view: ScopesView) -> None:
        t = self._chain_table
        assert t is not None
        t.setRowCount(len(view.chain))
        for i, scope in enumerate(view.chain):
            if scope == "facility":
                label = f"Facility ({view.facility_label})" if view.facility_label else "Facility"
            elif scope.startswith("user:"):
                label = f"Me ({scope})"
            else:
                label = scope
            t.setItem(i, 0, QTableWidgetItem(label))
            t.setItem(i, 1, QTableWidgetItem(view.roles.get(scope) or "—"))

    def _fill_keys(self, view: ScopesView) -> None:
        t = self._keys_table
        assert t is not None
        t.setRowCount(len(view.keys))
        for i, row in enumerate(view.keys):
            value = QTableWidgetItem(format_value(row.value))
            if row.rejected_at:
                value.setToolTip("Ignored invalid value at: " + ", ".join(row.rejected_at))
            locked = QTableWidgetItem(f"🔒 {row.locked_at}" if row.locked_at else "")
            locked.setToolTip(row.lock_reason)
            cells = [
                QTableWidgetItem(row.key),
                QTableWidgetItem(row.status),
                QTableWidgetItem(", ".join(row.scopes)),
                value,
                QTableWidgetItem(row.origin or ("default" if row.status in
                                                ("core", "approved", "undeclared") else "")),
                locked,
            ]
            for c, item in enumerate(cells):
                t.setItem(i, c, item)
