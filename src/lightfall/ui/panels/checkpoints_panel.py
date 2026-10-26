"""Checkpoints panel: browse the position checkpoint log, diff a checkpoint
against current positions, and queue a restore of the positioners you tick."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lightfall.acquire.checkpoints import Checkpoint, CheckpointStore, compare
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.utils.logging import logger

_COLUMNS = ("Restore", "Positioner", "Saved", "Current", "Δ")


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"


class CheckpointsPanel(BasePanel):
    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.checkpoints",
        name="Checkpoints",
        description="Saved positioner positions: compare with now and restore.",
        icon="history",
        category="Control",
        default_area="right",
        proactive_init=False,
    )

    def _setup_ui(self) -> None:
        store = CheckpointStore.get_instance()

        buttons = QHBoxLayout()
        self._capture_btn = QPushButton("Checkpoint now")
        self._capture_btn.setObjectName("checkpointCaptureButton")
        self._capture_btn.clicked.connect(self._on_capture)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._show_selected)
        self._restore_btn = QPushButton("Restore selected…")
        self._restore_btn.setObjectName("checkpointRestoreButton")
        self._restore_btn.clicked.connect(self._on_restore)
        for b in (self._capture_btn, self._refresh_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self._restore_btn)

        self._list = QListWidget()
        self._list.setObjectName("checkpointList")
        self._list.currentItemChanged.connect(lambda *_: self._show_selected())

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self._detail = QLabel("Select a checkpoint.")
        self._detail.setWordWrap(True)
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("checkpointTable")
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self._detail)
        rl.addWidget(self._table, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._list)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 2)

        self._layout.addLayout(buttons)
        self._layout.addWidget(splitter, 1)

        for cp in store.list():
            self._append(cp, at_top=False)
        store.added.connect(self._on_added)
        if self._list.count():
            self._list.setCurrentRow(0)

    # --- list ---------------------------------------------------------

    def _append(self, cp: Checkpoint, *, at_top: bool) -> None:
        item = QListWidgetItem(f"{cp.display_time}  {cp.label}  [{cp.source}]")
        item.setData(Qt.ItemDataRole.UserRole, cp.id)
        item.setToolTip(cp.reason or cp.label)
        if at_top:
            self._list.insertItem(0, item)
        else:
            self._list.addItem(item)

    def _on_added(self, cp: Checkpoint) -> None:
        self._append(cp, at_top=True)

    def selected_checkpoint(self) -> Checkpoint | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return CheckpointStore.get_instance().get(item.data(Qt.ItemDataRole.UserRole))

    # --- detail -------------------------------------------------------

    def _show_selected(self) -> None:
        cp = self.selected_checkpoint()
        self._table.setRowCount(0)
        if cp is None:
            self._detail.setText("Select a checkpoint.")
            return
        rows = compare(cp)
        changed = sum(r["changed"] for r in rows)
        note = f" · could not read: {', '.join(cp.skipped)}" if cp.skipped else ""
        self._detail.setText(
            f"<b>{cp.label}</b> — {cp.display_time} ({cp.source})<br>"
            f"{len(rows)} positioners, {changed} changed since{note}"
            + (f"<br><i>{cp.reason}</i>" if cp.reason else "")
        )
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if r["changed"] else Qt.CheckState.Unchecked)
            self._table.setItem(i, 0, check)
            self._table.setItem(i, 1, QTableWidgetItem(r["name"]))
            self._table.setItem(i, 2, QTableWidgetItem(_fmt(r["saved"])))
            self._table.setItem(i, 3, QTableWidgetItem(_fmt(r["current"])))
            delta = None if r["current"] is None else r["current"] - r["saved"]
            self._table.setItem(i, 4, QTableWidgetItem(_fmt(delta) if r["changed"] else ""))

    def checked_names(self) -> list[str]:
        names = []
        for i in range(self._table.rowCount()):
            if self._table.item(i, 0).checkState() == Qt.CheckState.Checked:
                names.append(self._table.item(i, 1).text())
        return names

    # --- actions ------------------------------------------------------

    def _on_capture(self) -> None:
        CheckpointStore.get_instance().capture("manual checkpoint", source="user")

    def _on_restore(self) -> None:
        cp = self.selected_checkpoint()
        names = self.checked_names()
        if cp is None or not names:
            QMessageBox.information(self, "Restore", "Tick the positioners to restore first.")
            return
        lines = "\n".join(f"  {n} → {_fmt(cp.positions[n])}" for n in names[:30])
        more = f"\n  … and {len(names) - 30} more" if len(names) > 30 else ""
        reply = QMessageBox.question(
            self,
            "Restore checkpoint",
            f"Queue a plan that moves {len(names)} positioner(s) back to "
            f"'{cp.label}' ({cp.display_time})?\n\n{lines}{more}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.restore(cp, names)

    def restore(self, cp: Checkpoint, names: list[str]) -> str | None:
        from lightfall.plugins.agents.checkpoint_tools import submit_restore

        try:
            proc_id, _targets = submit_restore(cp, names, reason=f"Restore checkpoint '{cp.label}'")
        except Exception as e:  # noqa: BLE001
            logger.exception("checkpoint restore failed")
            QMessageBox.warning(self, "Restore failed", str(e))
            return None
        return proc_id
