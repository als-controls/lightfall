"""Pipeline Jobs dock panel - queue + recent jobs table."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


_COLUMNS = ["job_id", "pipeline", "input_uid", "status", "started", "outputs"]


class PipelineJobsPanel(QWidget):
    def __init__(self, *, client: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._client = client
        self._rows: List[Dict[str, Any]] = []

        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        self._queue_label = QLabel("Queue: 0")
        header.addWidget(self._queue_label)
        header.addStretch()
        outer.addLayout(header)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        outer.addWidget(self._table)

        client.sigJobQueued.connect(self._on_queued)
        client.sigJobProgress.connect(self._on_progress)
        client.sigJobCompleted.connect(self._on_completed)
        client.sigJobFailed.connect(self._on_failed)

    def row_count(self) -> int:
        return len(self._rows)

    def row(self, index: int) -> Dict[str, Any]:
        return self._rows[index]

    def _find_row(self, job_id: str) -> Optional[int]:
        for i, r in enumerate(self._rows):
            if r["job_id"] == job_id:
                return i
        return None

    def _add_row(self, data: Dict[str, Any]) -> None:
        self._rows.append(data)
        row = self._table.rowCount()
        self._table.insertRow(row)
        for col, key in enumerate(_COLUMNS):
            self._table.setItem(row, col, QTableWidgetItem(str(data.get(key, ""))))

    def _update_row(self, index: int) -> None:
        data = self._rows[index]
        for col, key in enumerate(_COLUMNS):
            self._table.item(index, col).setText(str(data.get(key, "")))

    def _refresh_queue_label(self) -> None:
        active = sum(
            1 for r in self._rows
            if r.get("status") in ("queued", "running", "env_building")
        )
        self._queue_label.setText(f"Queue: {active}")

    def _on_queued(self, evt: Dict[str, Any]) -> None:
        self._add_row({
            "job_id": evt.get("job_id", ""),
            "pipeline": evt.get("pipeline", ""),
            "input_uid": evt.get("input_run_uid", ""),
            "status": "queued",
            "started": "",
            "outputs": "",
            "output_count": 0,
        })
        self._refresh_queue_label()

    def _on_progress(self, evt: Dict[str, Any]) -> None:
        job_id = evt.get("job_id", "")
        if not job_id:
            return
        idx = self._find_row(job_id)
        if idx is None:
            self._add_row({
                "job_id": job_id, "pipeline": "",
                "input_uid": evt.get("input_run_uid", ""),
                "status": evt.get("status", ""), "started": "", "outputs": "",
                "output_count": 0,
            })
            idx = self._find_row(job_id)
        self._rows[idx]["status"] = evt.get("status", self._rows[idx]["status"])
        self._update_row(idx)
        self._refresh_queue_label()

    def _on_completed(self, evt: Dict[str, Any]) -> None:
        idx = self._find_row(evt.get("job_id", ""))
        if idx is None:
            return
        uids = evt.get("output_run_uids", []) or []
        self._rows[idx].update({
            "status": "completed",
            "outputs": ", ".join(u[:8] for u in uids),
            "output_count": len(uids),
        })
        self._update_row(idx)
        self._refresh_queue_label()

    def _on_failed(self, evt: Dict[str, Any]) -> None:
        idx = self._find_row(evt.get("job_id", ""))
        if idx is None:
            return
        self._rows[idx]["status"] = "failed"
        self._update_row(idx)
        self._refresh_queue_label()
