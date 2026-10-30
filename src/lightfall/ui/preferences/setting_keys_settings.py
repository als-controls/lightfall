"""Preferences page where facility admins review proposed setting keys.

A key above user scope must be approved before it can be written there. Writing one
proposes it (the client sends the rules it declares); this page lists the proposals and
approved keys, and lets a facility admin approve (optionally changing the rules), reject
or revoke them. The logbook enforces the role; this page only hides what won't work.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import shiboken6
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
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

LEVELS = ("facility", "beamline", "branch", "user")
WRITE_ROLES = ("operator", "maintainer", "admin", "owner")
FILTERS = (("Pending", "pending"), ("Approved", "approved"), ("Rejected", "rejected"),
           ("All", None))


@dataclass(frozen=True)
class KeysState:
    records: tuple[dict[str, Any], ...]
    facility_admin: bool
    restricted: bool  # the caller may not list keys (needs maintainer somewhere)


def review_base(record: Mapping[str, Any]) -> dict[str, Any]:
    """The rules an approval starts from: the approved spec, else the proposed one."""
    base = record.get("spec") if record.get("status") == "approved" else None
    base = base or record.get("proposed_spec") or {}
    return dict(base) if isinstance(base, Mapping) else {}


def rule_edits(base: Mapping[str, Any], *, scopes: set[str], write_role: str,
               operator_writable: bool, lockable: bool) -> dict[str, Any]:
    """Only the rule fields that differ from ``base`` (the server's overridable fields)."""
    wanted = {"scopes": sorted(scopes), "write_role": write_role,
              "operator_writable": operator_writable, "lockable": lockable}
    current = {
        "scopes": sorted(base.get("scopes") or ["user"]),
        "write_role": base.get("write_role") if isinstance(base.get("write_role"), str)
        else "maintainer",
        "operator_writable": bool(base.get("operator_writable", False)),
        "lockable": bool(base.get("lockable", True)),
    }
    return {k: v for k, v in wanted.items() if v != current[k]}


def _default_loader() -> KeysState:
    from lightfall_settings import role_at_least

    from lightfall.scopes.client import ScopesClient

    client = ScopesClient()
    info = client.chain("facility")
    records = client.setting_keys()
    admin = role_at_least(info.roles.get("facility"), "admin")
    return KeysState(records=tuple(records or ()), facility_admin=admin,
                     restricted=records is None)


def _default_actions() -> Any:
    from lightfall.scopes.client import ScopesClient

    return ScopesClient()


class SettingKeysSettingsPlugin(SettingsPlugin):
    """Review page for proposed and approved setting keys."""

    def __init__(self) -> None:
        self.loader: Callable[[], KeysState] = _default_loader
        self.actions: Callable[[], Any] = _default_actions
        self._widget: QWidget | None = None
        self._status: QLabel | None = None
        self._filter: QComboBox | None = None
        self._table: QTableWidget | None = None
        self._rules: QGroupBox | None = None
        self._levels: dict[str, QCheckBox] = {}
        self._write_role: QComboBox | None = None
        self._operator_writable: QCheckBox | None = None
        self._lockable: QCheckBox | None = None
        self._note: QLineEdit | None = None
        self._approve: QPushButton | None = None
        self._reject: QPushButton | None = None
        self._revoke: QPushButton | None = None
        self._state = KeysState((), False, False)
        self._shown: list[dict[str, Any]] = []
        self._futures: list[QThreadFuture] = []
        self._generation = 0

    @property
    def name(self) -> str:
        return "setting_keys"

    @property
    def display_name(self) -> str:
        return "Setting keys"

    @property
    def category(self) -> str:
        return "advanced"

    @property
    def priority(self) -> int:
        return 91

    def load_settings(self) -> None:
        pass

    def save_settings(self) -> None:
        pass

    # ── widget ──────────────────────────────────────────────────────────

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self._widget = widget
        widget.destroyed.connect(self._on_widget_destroyed)

        self._status = QLabel(widget)
        self._status.setObjectName("keysStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        if not scopes_available():
            self._status.setText(
                "Scoped settings unavailable: install the lightfall-settings package.")
            return widget

        row = QHBoxLayout()
        row.addWidget(QLabel("Show:", widget))
        self._filter = QComboBox(widget)
        self._filter.setObjectName("keysFilter")
        for label, value in FILTERS:
            self._filter.addItem(label, value)
        self._filter.currentIndexChanged.connect(lambda _i: self._fill_table())
        row.addWidget(self._filter)
        row.addStretch(1)
        refresh = QPushButton("Refresh", widget)
        refresh.clicked.connect(self._refresh)
        row.addWidget(refresh)
        layout.addLayout(row)

        self._table = QTableWidget(0, 6, widget)
        self._table.setObjectName("keysTable")
        self._table.setHorizontalHeaderLabels(
            ["Key", "Status", "Proposed by", "At scope", "Levels", "Note"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._on_selected)
        layout.addWidget(self._table, 1)

        layout.addWidget(self._create_rules(widget))
        self._refresh()
        return widget

    def _create_rules(self, parent: QWidget) -> QGroupBox:
        group = QGroupBox("Rules", parent)
        form = QFormLayout(group)
        levels = QHBoxLayout()
        for level in LEVELS:
            box = QCheckBox(level, group)
            box.setObjectName(f"keysLevel_{level}")
            self._levels[level] = box
            levels.addWidget(box)
        levels.addStretch(1)
        form.addRow("Settable at:", levels)
        self._write_role = QComboBox(group)
        self._write_role.setObjectName("keysWriteRole")
        self._write_role.addItems(WRITE_ROLES)
        form.addRow("Who may write it:", self._write_role)
        self._operator_writable = QCheckBox("Operators may write it at every level", group)
        form.addRow(self._operator_writable)
        self._lockable = QCheckBox("Admins may lock it", group)
        form.addRow(self._lockable)
        self._note = QLineEdit(group)
        self._note.setObjectName("keysNote")
        self._note.setPlaceholderText("Note (shown with the decision)")
        form.addRow("Note:", self._note)
        buttons = QHBoxLayout()
        self._approve = QPushButton("Approve", group)
        self._approve.setObjectName("keysApprove")
        self._approve.clicked.connect(self._on_approve)
        self._reject = QPushButton("Reject", group)
        self._reject.setObjectName("keysReject")
        self._reject.clicked.connect(self._on_reject)
        self._revoke = QPushButton("Revoke", group)
        self._revoke.setObjectName("keysRevoke")
        self._revoke.setToolTip("Forget the approval: the key goes back to user-only")
        self._revoke.clicked.connect(self._on_revoke)
        for b in (self._approve, self._reject, self._revoke):
            buttons.addWidget(b)
        buttons.addStretch(1)
        form.addRow(buttons)
        self._rules = group
        group.setEnabled(False)
        return group

    def _on_widget_destroyed(self, *_: Any) -> None:
        self._generation += 1
        self._widget = self._status = self._filter = self._table = self._rules = None
        self._write_role = self._operator_writable = self._lockable = self._note = None
        self._approve = self._reject = self._revoke = None
        self._levels = {}

    def _alive(self) -> bool:
        return self._widget is not None and shiboken6.isValid(self._widget)

    # ── loading ─────────────────────────────────────────────────────────

    def _run(self, job: Callable[[], Any], done: Callable[[Any], None]) -> None:
        self._generation += 1
        gen = self._generation
        future = QThreadFuture(
            job,
            callback_slot=lambda result, g=gen: done(result) if g == self._generation else None,
            except_slot=lambda exc, g=gen: self._on_error(exc) if g == self._generation else None,
            name="setting-keys", log_exceptions=False,
        )
        self._futures = [f for f in self._futures if f.isRunning()] + [future]
        future.start()

    def _refresh(self) -> None:
        if self._status is not None:
            self._status.setText("Loading…")
        self._run(self.loader, self._on_state)

    def _on_state(self, state: KeysState) -> None:
        if not self._alive() or self._status is None:
            return
        self._state = state
        if state.restricted:
            self._status.setText("Listing setting keys needs a maintainer role somewhere.")
        elif state.facility_admin:
            self._status.setText("You are a facility admin: you can approve, reject and "
                                 "revoke setting keys.")
        else:
            self._status.setText("Only facility admins can approve setting keys; you can "
                                 "see them here.")
        self._fill_table()

    def _on_error(self, exc: Exception) -> None:
        if not self._alive() or self._status is None:
            return
        status = getattr(exc, "status", None)
        self._status.setText("Log in to review setting keys" if status == 401 else str(exc))
        logger.info("Setting keys page: {}", exc)

    def _fill_table(self) -> None:
        table = self._table
        if table is None or self._filter is None:
            return
        wanted = self._filter.currentData()
        self._shown = [r for r in self._state.records
                       if wanted is None or r.get("status") == wanted]
        table.setRowCount(len(self._shown))
        for i, rec in enumerate(self._shown):
            levels = ", ".join(review_base(rec).get("scopes") or [])
            cells = [rec.get("key"), rec.get("status"), rec.get("proposed_by"),
                     rec.get("proposed_scope"), levels, rec.get("note")]
            for c, text in enumerate(cells):
                table.setItem(i, c, QTableWidgetItem(str(text or "")))
        self._on_selected()

    def _selected(self) -> dict[str, Any] | None:
        if self._table is None:
            return None
        rows = self._table.selectionModel().selectedRows()
        index = rows[0].row() if rows else -1
        return self._shown[index] if 0 <= index < len(self._shown) else None

    def _on_selected(self) -> None:
        rec = self._selected()
        if self._rules is None:
            return
        self._rules.setEnabled(rec is not None and self._state.facility_admin)
        if rec is None:
            return
        base = review_base(rec)
        scopes = set(base.get("scopes") or ["user"])
        for level, box in self._levels.items():
            box.setChecked(level in scopes)
        role = base.get("write_role") if isinstance(base.get("write_role"), str) else "maintainer"
        assert self._write_role and self._operator_writable and self._lockable and self._note
        self._write_role.setCurrentText(role)
        self._operator_writable.setChecked(bool(base.get("operator_writable", False)))
        self._lockable.setChecked(bool(base.get("lockable", True)))
        self._note.setText("")
        status = rec.get("status")
        assert self._approve and self._reject and self._revoke
        self._approve.setText("Save rules" if status == "approved" else "Approve")
        self._reject.setEnabled(status == "pending")
        self._revoke.setEnabled(status == "approved")

    # ── decisions ───────────────────────────────────────────────────────

    def _edits(self, rec: Mapping[str, Any]) -> dict[str, Any]:
        assert self._write_role and self._operator_writable and self._lockable
        return rule_edits(
            review_base(rec),
            scopes={lvl for lvl, box in self._levels.items() if box.isChecked()},
            write_role=self._write_role.currentText(),
            operator_writable=self._operator_writable.isChecked(),
            lockable=self._lockable.isChecked(),
        )

    def _note_text(self) -> str:
        return self._note.text().strip() if self._note is not None else ""

    def _on_approve(self) -> None:
        rec = self._selected()
        if rec is None:
            return
        if not any(box.isChecked() for box in self._levels.values()):
            QMessageBox.warning(self._widget, "Setting keys", "Pick at least one level.")
            return
        key, edits, note = str(rec["key"]), self._edits(rec), self._note_text()
        actions = self.actions
        self._run(lambda: actions().approve_key(key, edits=edits, note=note),
                  lambda _r: self._refresh())

    def _on_reject(self) -> None:
        rec = self._selected()
        if rec is None:
            return
        key, note = str(rec["key"]), self._note_text()
        actions = self.actions
        self._run(lambda: actions().reject_key(key, note=note), lambda _r: self._refresh())

    def _on_revoke(self) -> None:
        rec = self._selected()
        if rec is None:
            return
        key = str(rec["key"])
        answer = QMessageBox.question(
            self._widget, "Revoke setting key",
            f"Revoke {key}? It goes back to user-only: values set for a facility, beamline "
            "or branch stop applying.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        actions = self.actions
        self._run(lambda: actions().revoke_key(key), lambda _r: self._refresh())
