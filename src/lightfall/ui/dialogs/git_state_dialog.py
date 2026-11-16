"""Popup listing source checkouts in a state worth knowing about, with a Push button per repo."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lightfall.github.push import PushResult, PushTarget, push_repo, push_target
from lightfall.github.session import GitHubSession, get_github_session, scrub
from lightfall.ui.dialogs.base import LFDialog
from lightfall.ui.dialogs.github_sign_in import start_sign_in
from lightfall.updates.git import RepoState
from lightfall.utils.crash_diagnostics import gui_thread_only
from lightfall.utils.threads import QThreadFuture

COLUMNS = ["Repo", "Branch", "State", "Path", "", ""]
STATE_COL = 2
PUSH_COL = len(COLUMNS) - 1


def describe_state(repo: RepoState) -> str:
    """Local changes and unpushed commits, e.g. "uncommitted changes, ahead 2"."""
    parts = []
    if repo.dirty:
        parts.append("uncommitted changes")
    if repo.ahead:
        parts.append(f"ahead {repo.ahead}")
    return ", ".join(parts)


def _owner_repo(url: str) -> str:
    """``github.com/<owner>/<repo>`` from an https remote URL."""
    owner, name = url.removesuffix(".git").rstrip("/").rsplit("/", 2)[-2:]
    return f"github.com/{owner}/{name}"


class GitStateDialog(LFDialog):
    """Shows each noteworthy repo's branch and state, and can push its current branch."""

    def __init__(
        self,
        repos: list[RepoState],
        parent: QWidget | None = None,
        *,
        checker: Any | None = None,
        github_session: GitHubSession | None = None,
        push: Callable[..., PushResult] = push_repo,
        target: Callable[[Path], PushTarget | str] = push_target,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Lightfall source checkouts")
        self.resize(820, 300)
        self._repos = list(repos)
        self._checker = checker
        self._session = github_session if github_session is not None else get_github_session()
        self._push = push
        self._target = target
        self._targets: list[PushTarget | str] | None = None
        self._futures: list[QThreadFuture] = []
        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(repos), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, repo in enumerate(repos):
            values = [repo.name, repo.branch or "detached HEAD", describe_state(repo),
                      str(repo.root)]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
            button = QPushButton("Open folder")
            button.clicked.connect(
                lambda _checked=False, root=repo.root: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(root))))
            self.table.setCellWidget(row, PUSH_COL - 1, button)
        self._rebuild_push_buttons()
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._session.changed.connect(self._rebuild_push_buttons)
        self._start_target_worker()

    # --- targets -----------------------------------------------------------------

    def _start_target_worker(self) -> None:
        roots = [r.root for r in self._repos]
        target = self._target

        def work() -> list[PushTarget | str]:
            return [target(root) for root in roots]

        future = QThreadFuture(work, callback_slot=self._on_targets,
                               except_slot=self._on_targets_error, name="git_push_targets")
        self._futures.append(future)
        future.start()

    @gui_thread_only
    def _on_targets(self, targets: list[PushTarget | str]) -> None:
        self._targets = targets
        self._rebuild_push_buttons()

    @gui_thread_only
    def _on_targets_error(self, exc: Exception) -> None:
        self._targets = [f"Could not inspect repo: {scrub(str(exc))}"] * len(self._repos)
        self._rebuild_push_buttons()

    # --- buttons -----------------------------------------------------------------

    def _make_button(self, row: int) -> QPushButton:
        button = QPushButton()
        if self._targets is None:
            button.setText("Checking…")
            button.setEnabled(False)
            return button
        target = self._targets[row]
        if isinstance(target, str):
            button.setText("Push")
            button.setEnabled(False)
            button.setToolTip(target)
            return button
        if self._checker is not None and not self._checker.is_allowed():
            button.setText("Push")
            button.setEnabled(False)
            button.setToolTip("Pushing requires an administrator session that is online.")
            return button
        cred = self._session.credential()
        if cred is None:
            button.setText("Sign in to push")
            button.clicked.connect(lambda _checked=False: start_sign_in(self, self._session))
        else:
            button.setText("Push")
            button.clicked.connect(lambda _checked=False, r=row: self._confirm_push(r))
        return button

    @gui_thread_only
    def _rebuild_push_buttons(self) -> None:
        for row in range(self.table.rowCount()):
            old = self.table.cellWidget(row, PUSH_COL)
            if old is not None and old.property("pushing"):
                continue  # leave a running push's disabled button alone
            self.table.setCellWidget(row, PUSH_COL, self._make_button(row))
        self.table.resizeColumnToContents(PUSH_COL)

    # --- push --------------------------------------------------------------------

    def _confirm_push(self, row: int) -> None:
        target = self._targets[row] if self._targets else None
        cred = self._session.credential()
        allowed = self._checker is None or self._checker.is_allowed()
        if not isinstance(target, PushTarget) or cred is None or not allowed:
            self._rebuild_push_buttons()
            return
        repo = self._repos[row]
        answer = QMessageBox.question(
            self, "Push to GitHub",
            f"Push {target.branch} of {repo.name} to {_owner_repo(target.url)} as {cred.login}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        button = self.table.cellWidget(row, PUSH_COL)
        if button is not None:
            button.setEnabled(False)
            button.setProperty("pushing", True)
        self.table.item(row, STATE_COL).setText("Pushing…")
        push = self._push

        def work() -> tuple[int, PushResult]:
            # Never pass url=: that override skips the redirect/TLS safety checks.
            return row, push(target, cred)

        future = QThreadFuture(work, callback_slot=self._on_pushed,
                               except_slot=lambda exc, r=row: self._on_push_error(r, exc),
                               name="git_push")
        self._futures.append(future)
        future.start()

    def _finish_row(self, row: int, ok: bool, message: str) -> None:
        item = self.table.item(row, STATE_COL)
        if item is not None:
            item.setText(f"{'✓' if ok else '✗'} {scrub(message)}")
        button = self.table.cellWidget(row, PUSH_COL)
        if button is not None:
            button.setProperty("pushing", False)
        self._rebuild_push_buttons()

    @gui_thread_only
    def _on_pushed(self, payload: tuple[int, PushResult]) -> None:
        row, result = payload
        self._finish_row(row, result.ok, result.message)
        if result.ok and self._checker is not None:
            self._checker.check_now(fetch=True)

    @gui_thread_only
    def _on_push_error(self, row: int, exc: Exception) -> None:
        self._finish_row(row, False, f"push failed: {exc}")
