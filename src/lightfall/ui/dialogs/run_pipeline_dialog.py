"""Run Pipeline dialog - picker + parameter form + submit."""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from lightfall.utils.threads import QThreadFuture


class RunPipelineDialog(QDialog):
    def __init__(
        self,
        *,
        client: Any,
        run_uid: str,
        input_access_blob: dict[str, Any],
        user_id: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._run_uid = run_uid
        self._blob = input_access_blob
        self.user_id = user_id
        self._submit_future: QThreadFuture | None = None

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
        self._param_widgets: dict[str, QLineEdit] = {}
        self._rebuild_param_form(0)

        self._status_label = QLabel("")
        outer.addWidget(self._status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        self._ok_button.setEnabled(bool(self._pipelines))
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

    def _collect_parameters(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, w in self._param_widgets.items():
            text = w.text()
            try:
                out[k] = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                out[k] = text
        return out

    def _submit(self) -> None:
        pipeline = self.pipeline_combo.currentText()
        if not pipeline:
            self.reject()
            return
        if self._submit_future is not None and self._submit_future.isRunning():
            # Defensive: guard against rapid OK clicks before slots fire.
            return
        # client.submit() makes a synchronous NATS ipc.request round-trip
        # that can block the Qt main thread for several seconds on a slow
        # network. Run it through lightfall.utils.threads.QThreadFuture so the
        # dialog stays responsive; results come back via callback_slot /
        # except_slot, which Qt marshals onto the main thread.
        self._ok_button.setEnabled(False)
        self._status_label.setText("Submitting...")
        self._submit_future = QThreadFuture(
            self._client.submit,
            pipeline=pipeline,
            input_run_uid=self._run_uid,
            parameters=self._collect_parameters(),
            input_access_blob=self._blob,
            user_id=self.user_id,
            callback_slot=self._on_submit_success,
            except_slot=self._on_submit_error,
            name=f"pipeline_submit_{pipeline}",
        )
        self._submit_future.start()

    def _on_submit_success(self, _job_id: Any) -> None:
        self._status_label.setText("")
        self.accept()

    def _on_submit_error(self, exc: BaseException) -> None:
        self._status_label.setText("")
        self._ok_button.setEnabled(True)
        QMessageBox.critical(self, "Pipeline error", str(exc))
