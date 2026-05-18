"""Pipeline Triggers settings panel."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lucid.acquire.triggers.filter import FilterPredicate
from lucid.acquire.triggers.run_end import RunEndTrigger
from lucid.acquire.triggers.run_start import RunStartTrigger


_COLUMNS = ["type", "filter", "pipeline", "parameter_overrides"]


def _construct_trigger(spec: Dict[str, Any]):
    f = FilterPredicate(**spec.get("filter", {}))
    if spec["type"] == "run_start":
        return RunStartTrigger(filter=f, pipeline=spec["pipeline"],
                               parameter_overrides=spec.get("parameter_overrides", {}))
    return RunEndTrigger(filter=f, pipeline=spec["pipeline"],
                         parameter_overrides=spec.get("parameter_overrides", {}))


class PipelineTriggersPanel(QWidget):
    SETTINGS_KEY = "pipeline_triggers"

    def __init__(
        self,
        *,
        manager: Any,
        settings_backend: Any,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._backend = settings_backend
        self._specs: List[Dict[str, Any]] = []

        outer = QVBoxLayout(self)
        controls = QHBoxLayout()
        add_btn = QPushButton("Add...")
        add_btn.clicked.connect(self._open_add_dialog)
        controls.addWidget(add_btn)
        controls.addStretch()
        outer.addLayout(controls)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        outer.addWidget(self._table)

        for spec in self._backend.load() or []:
            self._add_row(spec, register_with_manager=True)

    def row_count(self) -> int:
        return len(self._specs)

    def add_trigger(self, spec: Dict[str, Any]) -> None:
        self._add_row(spec, register_with_manager=True)
        self._backend.save(self.SETTINGS_KEY, self._specs)

    def _add_row(self, spec: Dict[str, Any], *, register_with_manager: bool) -> None:
        self._specs.append(spec)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(spec.get("type", "")))
        self._table.setItem(row, 1, QTableWidgetItem(json.dumps(spec.get("filter", {}))))
        self._table.setItem(row, 2, QTableWidgetItem(spec.get("pipeline", "")))
        self._table.setItem(row, 3, QTableWidgetItem(json.dumps(spec.get("parameter_overrides", {}))))
        if register_with_manager:
            self._manager.add(_construct_trigger(spec))

    def _open_add_dialog(self) -> None:
        # Minimal stub. Full implementation should be a proper QDialog with
        # type/pipeline/filter/parameter fields. Out of MVP scope for Phase 1.
        pass
