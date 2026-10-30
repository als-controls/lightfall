"""Read-only popup listing source checkouts in a state worth knowing about."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.dialogs.base import LFDialog
from lightfall.updates.git import RepoState

COLUMNS = ["Repo", "Branch", "State", "Path", ""]


def describe_state(repo: RepoState) -> str:
    """Local changes and unpushed commits, e.g. "uncommitted changes, ahead 2"."""
    parts = []
    if repo.dirty:
        parts.append("uncommitted changes")
    if repo.ahead:
        parts.append(f"ahead {repo.ahead}")
    return ", ".join(parts)


class GitStateDialog(LFDialog):
    """Shows each noteworthy repo's branch and state; changes nothing."""

    def __init__(self, repos: list[RepoState], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lightfall source checkouts")
        self.resize(720, 300)
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
            self.table.setCellWidget(row, len(COLUMNS) - 1, button)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
