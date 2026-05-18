"""Run Pipeline dialog - picker + parameter form + submit."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QVBoxLayout, QWidget,
)


class RunPipelineDialog(QDialog):
    def __init__(
        self,
        *,
        client: Any,
        run_uid: str,
        input_access_blob: Dict[str, Any],
        user_id: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._run_uid = run_uid
        self._blob = input_access_blob
        self.user_id = user_id

        self.setWindowTitle("Run pipeline...")
        self._pipelines = client.list_available()

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(f"Input run: <code>{run_uid[:8]}...</code>"))

        self.pipeline_combo = QComboBox()
        for p in self._pipelines:
            self.pipeline_combo.addItem(p["name"], userData=p)
        outer.addWidget(self.pipeline_combo)
        self.pipeline_combo.currentIndexChanged.connect(self._rebuild_param_form)

        self._param_form_container = QWidget()
        outer.addWidget(self._param_form_container)
        self._param_widgets: Dict[str, QLineEdit] = {}
        self._rebuild_param_form(0)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _rebuild_param_form(self, _index: int) -> None:
        pipeline = self.pipeline_combo.currentData()
        # Tear down old form
        if self._param_form_container.layout() is not None:
            old = self._param_form_container.layout()
            while old.count():
                item = old.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            QWidget().setLayout(old)
        layout = QFormLayout(self._param_form_container)
        self._param_widgets = {}
        if not pipeline:
            return
        schema = pipeline.get("parameters_schema", {}) or {}
        for name, meta in schema.items():
            edit = QLineEdit(str(meta.get("default", "")))
            layout.addRow(name, edit)
            self._param_widgets[name] = edit

    def _collect_parameters(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, w in self._param_widgets.items():
            text = w.text()
            try:
                out[k] = json.loads(text)
            except Exception:
                out[k] = text
        return out

    def _submit(self) -> None:
        pipeline = self.pipeline_combo.currentText()
        if not pipeline:
            self.reject()
            return
        self._client.submit(
            pipeline=pipeline,
            input_run_uid=self._run_uid,
            parameters=self._collect_parameters(),
            input_access_blob=self._blob,
            user_id=self.user_id,
        )
        self.accept()
