"""Pipeline Triggers settings panel."""
from __future__ import annotations

import json
from typing import Any, ClassVar, Dict, List, Optional

from loguru import logger
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from lucid.acquire.triggers.filter import FilterPredicate
from lucid.acquire.triggers.run_end import RunEndTrigger
from lucid.acquire.triggers.run_start import RunStartTrigger
from lucid.ui.panels.base import BasePanel, PanelMetadata


_COLUMNS = ["type", "filter", "pipeline", "parameter_overrides"]


def _construct_trigger(spec: Dict[str, Any]):
    f = FilterPredicate(**spec.get("filter", {}))
    t = spec["type"]
    if t == "run_start":
        return RunStartTrigger(filter=f, pipeline=spec["pipeline"],
                               parameter_overrides=spec.get("parameter_overrides", {}))
    if t == "run_end":
        return RunEndTrigger(filter=f, pipeline=spec["pipeline"],
                             parameter_overrides=spec.get("parameter_overrides", {}))
    raise ValueError(f"Unknown trigger type: {t!r}")


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
        try:
            self._backend.save(self.SETTINGS_KEY, self._specs)
        except Exception as exc:
            logger.error(
                "PipelineTriggersPanel: failed to persist triggers: {}", exc,
            )

    def _add_row(self, spec: Dict[str, Any], *, register_with_manager: bool) -> None:
        if register_with_manager:
            self._manager.add(_construct_trigger(spec))  # raises → no state change
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(spec.get("type", "")))
        self._table.setItem(row, 1, QTableWidgetItem(json.dumps(spec.get("filter", {}))))
        self._table.setItem(row, 2, QTableWidgetItem(spec.get("pipeline", "")))
        self._table.setItem(row, 3, QTableWidgetItem(json.dumps(spec.get("parameter_overrides", {}))))
        self._specs.append(spec)

    def _open_add_dialog(self) -> None:
        from lucid.ui.dialogs.add_trigger_dialog import AddTriggerDialog

        dialog = AddTriggerDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        spec = dialog.spec()
        if spec is None:
            return
        try:
            self.add_trigger(spec)
        except Exception as exc:
            logger.error("PipelineTriggersPanel: add_trigger failed: {}", exc)
            QMessageBox.critical(self, "Trigger error", str(exc))


class _PreferencesTriggerBackend:
    """Settings backend adapter that maps PipelineTriggersPanel's load/save
    onto PreferencesManager. The panel persists a single list of trigger
    specs under the SETTINGS_KEY, which we stash via PreferencesManager.set.
    """

    def __init__(self, prefs: Any, key: str) -> None:
        self._prefs = prefs
        self._key = key

    def load(self) -> List[Dict[str, Any]]:
        value = self._prefs.get(self._key, [])
        return list(value) if isinstance(value, list) else []

    def save(self, key: str, value: Any) -> None:
        self._prefs.set(key, value)


class PipelineTriggersDockPanel(BasePanel):
    """BasePanel wrapper that surfaces PipelineTriggersPanel inside the docking system.

    Pulls the TriggerManager singleton from ServiceRegistry and the
    PreferencesManager-backed settings backend. Renders a placeholder
    label when either dependency is unavailable, instead of crashing
    plug-in load.
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.pipeline_triggers",
        name="Pipeline Triggers",
        description="Manage automatic pipeline submissions on engine events",
        icon="zap",
        category="Acquisition",
        singleton=True,
        closable=True,
        keywords=["pipeline", "trigger", "automatic", "runengine"],
        default_area="left",
        sidebar_group="bottom",
        auto_hide=True,
        sidebar_order=20,
    )

    def _setup_ui(self) -> None:
        from lucid.acquire.triggers.manager import TriggerManager
        from lucid.core.services import ServiceRegistry
        from lucid.ui.preferences import PreferencesManager

        services = ServiceRegistry.get_instance()
        manager = services.get(TriggerManager, None)
        if manager is None:
            self._layout.addWidget(
                QLabel("TriggerManager is not registered.")
            )
            return
        prefs = services.get(PreferencesManager, None) or PreferencesManager.get_instance()
        backend = _PreferencesTriggerBackend(
            prefs=prefs, key=PipelineTriggersPanel.SETTINGS_KEY,
        )
        self._inner = PipelineTriggersPanel(
            manager=manager, settings_backend=backend,
        )
        self._layout.addWidget(self._inner)
