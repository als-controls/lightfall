"""Revision history of a shared item, with Restore and Retract for maintainers."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lightfall.content import actions
from lightfall.content.actions import ContentRow
from lightfall.content.labels import describe_source
from lightfall.scopes.client import ScopesError
from lightfall.ui.dialogs.base import LFDialog
from lightfall.ui.dialogs.content_publish_dialog import (
    Runner,
    error_text,
    retract_question,
    thread_runner,
    toast_success,
)
from lightfall.ui.theme import scaled_px

_COLUMNS = ("Revision", "When", "By", "Author", "Message", "Agent", "Current")


class ContentHistoryDialog(LFDialog):
    def __init__(self, row: ContentRow, roles: Mapping[str, str | None], *, client: Any,
                 run: Runner | None = None, parent=None) -> None:
        super().__init__(parent)
        self._row = row
        self._client = client
        self._run: Runner = run or thread_runner
        self._can_write = actions._role_at_least(roles.get(row.source), actions.PUBLISH_ROLE)
        self._records: list[dict[str, Any]] = []
        self._notice = ""  # message to keep showing when the next reload lands
        self._alive = True
        self._busy = False

        self.setWindowTitle(f"History of {row.type} {row.name}")
        self.setModal(True)
        self.setMinimumSize(720, 360)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._setup_ui()
        self.finished.connect(lambda *_: setattr(self, "_alive", False))
        self._reload()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        header = QLabel(f"{self._row.type} {self._row.name} at "
                        f"{describe_source(self._row.source)}")
        header.setStyleSheet(f"font-size: {scaled_px(16)}px; font-weight: bold;")
        layout.addWidget(header)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._refresh)
        layout.addWidget(self._table)

        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: #cc0000; font-size: {scaled_px(12)}px;")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        buttons = QHBoxLayout()
        self._restore_btn = QPushButton("Restore")
        self._restore_btn.clicked.connect(self._on_restore)
        buttons.addWidget(self._restore_btn)
        self._retract_btn = QPushButton("Retract")
        self._retract_btn.clicked.connect(self._on_retract)
        buttons.addWidget(self._retract_btn)
        buttons.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._refresh()

    # -- state -------------------------------------------------------------------------

    def _selected(self) -> dict[str, Any] | None:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows or rows[0].row() >= len(self._records):
            return None
        return self._records[rows[0].row()]

    def _refresh(self) -> None:
        sel = self._selected()
        idle = self._can_write and not self._busy
        self._restore_btn.setEnabled(bool(idle and sel and not sel.get("current")))
        self._retract_btn.setEnabled(bool(idle and any(r.get("current") for r in self._records)))

    def _show_error(self, text: str) -> None:
        self._error_label.setText(text)
        self._error_label.setVisible(bool(text))

    def _fill(self, records: list[dict[str, Any]]) -> None:
        self._records = records
        self._table.setRowCount(len(records))
        for i, r in enumerate(records):
            cells = (r.get("revision"), r.get("published_at"), r.get("published_by"),
                     r.get("author"), r.get("message"), "yes" if r.get("agent") else "",
                     "current" if r.get("current") else "")
            for j, v in enumerate(cells):
                self._table.setItem(i, j, QTableWidgetItem("" if v is None else str(v)))
        self._refresh()

    # -- actions -----------------------------------------------------------------------

    def _reload(self) -> None:
        row = self._row

        def work() -> list[dict[str, Any]]:
            return actions.history(self._client, row.source, row.type, row.name)

        self._run(work, self._on_loaded, self._on_failed)

    def _on_loaded(self, records: list[dict[str, Any]]) -> None:
        if self._alive:
            self._busy = False  # after a 412 the dialog stayed busy until this reload
            self._show_error(self._notice)  # kept across a reload after a 412
            self._notice = ""
            self._fill(records)

    def _on_failed(self, exc: Exception) -> None:
        if not self._alive:
            return
        reloading = isinstance(exc, ScopesError) and exc.status == 412
        self._busy = reloading  # stays busy until the reload lands (_on_loaded) or fails
        if isinstance(exc, ScopesError) and exc.status == 404:
            # Opened from your own copy for a scope where it was never published.
            text = f"{self._row.name} isn't published at {describe_source(self._row.source)}."
        else:
            text = error_text(exc, self._row.source)
        self._show_error(text)
        self._refresh()
        self._notice = ""
        if reloading:
            # Someone changed it after we read the history: show what is current now,
            # so the next Restore/Retract expects that revision.
            self._notice = text
            self._reload()

    def _confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(self, title, text)
        return answer == QMessageBox.StandardButton.Yes

    def _shown_current(self) -> int | str:
        """The revision this dialog shows as current, ``"none"`` when it's retracted.

        Sent as the expected revision, so the server refuses (412) if someone changed
        the item after the history was read.
        """
        return next((int(r["revision"]) for r in self._records if r.get("current")), "none")

    def _on_restore(self) -> None:
        sel = self._selected()
        if sel is None or sel.get("current"):
            return
        rev, row = int(sel["revision"]), self._row
        expected = self._shown_current()
        label = describe_source(row.source)
        if not self._confirm("Restore revision",
                             f"Make revision {rev} of {row.name} current at {label}?"):
            return
        self._start(lambda: actions.restore(self._client, row.source, row.type, row.name, rev,
                                            expected_revision=expected),
                    "Restored", f"Restored {row.name} at {label} to revision {rev}")

    def _on_retract(self) -> None:
        row = self._row
        expected = self._shown_current()
        if expected == "none":
            return  # nothing current to retract (the button is disabled then)
        label = describe_source(row.source)
        if not self._confirm("Retract", retract_question(row.type, row.name, row.source)):
            return
        self._start(lambda: actions.retract(self._client, row.source, row.type, row.name,
                                            expected_revision=expected),
                    "Retracted", f"Retracted {row.name} at {label}")

    def _start(self, work, done_title: str, done_text: str) -> None:
        self._busy = True
        self._notice = ""
        self._show_error("")
        self._refresh()

        def ok(_record: Any) -> None:
            self._busy = False
            toast_success(done_title, done_text)  # even if the dialog is gone: it happened
            if self._alive:
                self._reload()

        self._run(work, ok, self._on_failed)
