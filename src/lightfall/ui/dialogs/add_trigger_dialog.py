"""Add Trigger dialog - capture a single trigger spec from the user.

The dialog produces a dict shaped like the entries
``PipelineTriggersPanel._construct_trigger`` understands::

    {
        "type": "run_start" | "run_end",
        "filter": {"plan_name": str|None, "tags_includes": list[str]|None,
                   "start_doc_match": dict|None},
        "pipeline": str,
        "parameter_overrides": dict,
    }

Empty filter fields are dropped so ``FilterPredicate(**filter)`` keeps its
match-all behaviour. JSON fields are parsed with friendly errors.
"""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

_TRIGGER_TYPES = ("run_start", "run_end")


class AddTriggerDialog(QDialog):
    """Modal form for creating a new trigger spec.

    Args:
        pipelines: Optional list of known pipeline names. When provided the
            dialog renders a ``QComboBox``; otherwise it falls back to a
            free-form ``QLineEdit`` so users can type a name even when the
            executor hasn't been queried.
        parent: Owning widget.
    """

    def __init__(
        self,
        *,
        pipelines: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add pipeline trigger")
        self._spec: dict[str, Any] | None = None

        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)

        self._type_combo = QComboBox()
        for t in _TRIGGER_TYPES:
            self._type_combo.addItem(t)
        form.addRow("Type", self._type_combo)

        if pipelines:
            self._pipeline_combo: QComboBox | None = QComboBox()
            for name in pipelines:
                self._pipeline_combo.addItem(name)
            self._pipeline_edit: QLineEdit | None = None
            form.addRow("Pipeline", self._pipeline_combo)
        else:
            self._pipeline_combo = None
            self._pipeline_edit = QLineEdit()
            self._pipeline_edit.setPlaceholderText("e.g. reduce_saxs")
            form.addRow("Pipeline", self._pipeline_edit)

        self._plan_name_edit = QLineEdit()
        self._plan_name_edit.setPlaceholderText("(any)")
        form.addRow("Filter: plan_name", self._plan_name_edit)

        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("comma-separated, e.g. saxs,2d")
        form.addRow("Filter: tags include", self._tags_edit)

        self._param_overrides_edit = QPlainTextEdit()
        self._param_overrides_edit.setPlaceholderText('{"roi_x": [0, 1024]}')
        self._param_overrides_edit.setMaximumHeight(80)
        form.addRow("Parameter overrides (JSON)", self._param_overrides_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def spec(self) -> dict[str, Any] | None:
        """Return the spec captured on accept, or ``None`` if cancelled."""
        return self._spec

    def _selected_pipeline(self) -> str:
        if self._pipeline_combo is not None:
            return self._pipeline_combo.currentText().strip()
        assert self._pipeline_edit is not None
        return self._pipeline_edit.text().strip()

    def _on_accept(self) -> None:
        pipeline = self._selected_pipeline()
        if not pipeline:
            QMessageBox.warning(
                self, "Invalid trigger", "Pipeline name is required.",
            )
            return

        filter_spec: dict[str, Any] = {}
        plan_name = self._plan_name_edit.text().strip()
        if plan_name:
            filter_spec["plan_name"] = plan_name
        tags_raw = self._tags_edit.text().strip()
        if tags_raw:
            filter_spec["tags_includes"] = [
                t.strip() for t in tags_raw.split(",") if t.strip()
            ]

        overrides_raw = self._param_overrides_edit.toPlainText().strip()
        if overrides_raw:
            try:
                overrides = json.loads(overrides_raw)
            except json.JSONDecodeError as exc:
                QMessageBox.warning(
                    self,
                    "Invalid trigger",
                    f"Parameter overrides must be valid JSON: {exc}",
                )
                return
            if not isinstance(overrides, dict):
                QMessageBox.warning(
                    self,
                    "Invalid trigger",
                    "Parameter overrides must be a JSON object.",
                )
                return
        else:
            overrides = {}

        self._spec = {
            "type": self._type_combo.currentText(),
            "filter": filter_spec,
            "pipeline": pipeline,
            "parameter_overrides": overrides,
        }
        self.accept()
