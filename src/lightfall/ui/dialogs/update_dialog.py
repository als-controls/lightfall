"""Dialog listing available Lightfall updates and applying them (self-update)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.dialogs.base import LFDialog
from lightfall.updates.apply import ItemResult, UpdateBlocked, apply_updates, scan_running
from lightfall.updates.git import RepoState
from lightfall.updates.model import CheckResult
from lightfall.updates.pypi import PypiState
from lightfall.utils.logging import logger
from lightfall.utils.restart import request_restart
from lightfall.utils.threads import QThreadFuture

COLUMNS = ["", "Package", "Type", "Current", "Available", "Status"]
BUSY_TEXT = "A scan is running — updates are disabled until it finishes."
BUSY_POLL_MS = 2000


def ask_restart(parent: QWidget | None) -> bool:
    """Ask whether to restart now; True for "Restart now"."""
    box = QMessageBox(parent)
    box.setWindowTitle("Lightfall updates")
    box.setText("Lightfall needs to restart to use the updates.")
    restart = box.addButton("Restart now", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(restart)
    box.exec()
    return box.clickedButton() is restart


class UpdateDialog(LFDialog):
    """Lists updatable repos and packages, applies the selected ones, offers a restart."""

    _log_line = Signal(str)

    def __init__(
        self,
        checker: Any,
        parent: QWidget | None = None,
        apply: Callable[..., list[ItemResult]] = apply_updates,
        busy: Callable[[], bool] = scan_running,
        ask_restart: Callable[[QWidget], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._checker = checker
        self._apply = apply
        self._busy = busy
        self._ask_restart = ask_restart or _default_ask_restart
        self._items: list[RepoState | PypiState] = []
        self._applying = False
        self._applied = False
        self._future: QThreadFuture | None = None
        self.setWindowTitle("Lightfall updates")
        self.resize(760, 420)
        self._build()
        self._log_line.connect(self._log.appendPlainText)
        checker.result_changed.connect(self._on_result)
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(BUSY_POLL_MS)
        self._busy_timer.timeout.connect(self._refresh_busy)
        self._busy_timer.start()
        self._connect_engine()
        self._populate(checker.result)

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self._banner = QLabel(BUSY_TEXT)
        self._banner.setWordWrap(True)
        self._banner.setVisible(False)
        layout.addWidget(self._banner)
        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self._table.itemChanged.connect(lambda _item: self._refresh_busy())
        layout.addWidget(self._table)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setVisible(False)
        layout.addWidget(self._log)
        buttons = QHBoxLayout()
        self._log_toggle = QPushButton("Show log")
        self._log_toggle.setCheckable(True)
        self._log_toggle.toggled.connect(self._log.setVisible)
        self._log_toggle.toggled.connect(
            lambda on: self._log_toggle.setText("Hide log" if on else "Show log"))
        buttons.addWidget(self._log_toggle)
        buttons.addStretch(1)
        self._check_button = QPushButton("Check now")
        self._check_button.clicked.connect(lambda: self._checker.check_now(True))
        self._update_button = QPushButton("Update selected")
        self._update_button.clicked.connect(self._on_update)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for button in (self._check_button, self._update_button, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def _connect_engine(self) -> None:
        import lightfall.acquire.engine as engine_mod

        engine = engine_mod._engine
        if engine is None:
            return
        for name in ("sigStateChanged", "sigQueueChanged"):
            signal = getattr(engine, name, None)
            if signal is not None:
                signal.connect(self._refresh_busy)

    # -- rows -----------------------------------------------------------------

    def _on_result(self, result: CheckResult) -> None:
        if not self._applied and not self._applying:
            self._populate(result)

    def _populate(self, result: CheckResult) -> None:
        self._table.blockSignals(True)
        self._items = [*result.repo_updates, *result.pypi_updates]
        self._table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            if isinstance(item, RepoState):
                kind, current = "source", item.head
                available = f"{item.remote_head} ({item.behind} commit(s) behind)"
                blockers = item.blockers
            else:
                kind, current, available, blockers = "pypi", item.installed, item.latest, []
            check = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsUserCheckable
            if not blockers:
                flags |= Qt.ItemFlag.ItemIsEnabled
            check.setFlags(flags)
            check.setCheckState(Qt.CheckState.Unchecked if blockers else Qt.CheckState.Checked)
            self._table.setItem(row, 0, check)
            values = [item.name, kind, current, available or "", "; ".join(blockers)]
            for col, value in enumerate(values, start=1):
                self._table.setItem(row, col, QTableWidgetItem(value))
        self._table.resizeColumnsToContents()
        self._table.blockSignals(False)
        self._refresh_busy()

    def row_names(self) -> list[str]:
        return [item.name for item in self._items]

    def _row(self, name: str) -> int:
        return self.row_names().index(name)

    def cell(self, name: str, column: str) -> str:
        return self._table.item(self._row(name), COLUMNS.index(column)).text()

    def status_text(self, name: str) -> str:
        return self.cell(name, "Status")

    def is_checkable(self, name: str) -> bool:
        return bool(self._table.item(self._row(name), 0).flags() & Qt.ItemFlag.ItemIsEnabled)

    def is_checked(self, name: str) -> bool:
        return self._table.item(self._row(name), 0).checkState() == Qt.CheckState.Checked

    def _selected(self) -> tuple[list[RepoState], list[PypiState]]:
        chosen = [item for item in self._items
                  if self.is_checkable(item.name) and self.is_checked(item.name)]
        return ([i for i in chosen if isinstance(i, RepoState)],
                [i for i in chosen if isinstance(i, PypiState)])

    def _set_status(self, name: str, text: str) -> None:
        self._table.item(self._row(name), COLUMNS.index("Status")).setText(text)

    # -- gating ---------------------------------------------------------------

    def _refresh_busy(self, *args: Any) -> None:
        busy = self._busy()
        self._banner.setVisible(busy)
        repos, pypi = self._selected()
        self._update_button.setEnabled(
            not busy and not self._applying and not self._applied and bool(repos or pypi))

    # -- applying -------------------------------------------------------------

    def _on_update(self) -> None:
        if not self._checker.is_allowed():
            logger.info("Update refused: the session no longer allows it")
            return
        if self._busy():
            self._refresh_busy()
            return
        repos, pypi = self._selected()
        if not (repos or pypi):
            return
        self._applying = True
        self._refresh_busy()
        for item in [*repos, *pypi]:
            self._set_status(item.name, "Updating…")
        emit = self._log_line.emit
        apply = self._apply

        def work() -> list[ItemResult]:
            return apply(repos, pypi, log=emit)

        self._future = QThreadFuture(work, callback_slot=self._on_applied,
                                     except_slot=self._on_apply_error, name="update_apply")
        self._future.start()

    def _on_apply_error(self, exc: Exception) -> None:
        self._applying = False
        message = str(exc) if isinstance(exc, UpdateBlocked) else f"error: {exc}"
        self._log.appendPlainText(f"Update stopped: {message}")
        self._refresh_busy()

    def _on_applied(self, results: list[ItemResult]) -> None:
        self._applying = False
        self._applied = True
        names = self.row_names()
        for result in results:
            if result.name not in names:
                continue
            if not result.ok:
                text = f"Failed: {result.message}"
            else:
                text = "Updated" if result.changed else "Already current"
            self._set_status(result.name, text)
        self._refresh_busy()
        if any(r.changed for r in results):
            if self._ask_restart(self) and not self._busy():
                request_restart()
            else:
                self._checker.mark_restart_pending()
        self._checker.check_now(False)


_default_ask_restart = ask_restart
