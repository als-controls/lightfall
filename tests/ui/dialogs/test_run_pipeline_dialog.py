"""Tests for the Run Pipeline dialog."""
from unittest.mock import MagicMock

import pytest

from lucid.ui.dialogs.run_pipeline_dialog import RunPipelineDialog


def test_dialog_lists_pipelines(qtbot):
    client = MagicMock()
    client.list_available.return_value = [
        {"name": "reduce_saxs", "description": "x",
         "parameters_schema": {"roi_x": {"type": "array<int>", "default": [0, 1024]}}},
        {"name": "qc", "description": "y", "parameters_schema": {}},
    ]
    dialog = RunPipelineDialog(client=client, run_uid="abc", input_access_blob={})
    qtbot.addWidget(dialog)
    items = [dialog.pipeline_combo.itemText(i) for i in range(dialog.pipeline_combo.count())]
    assert "reduce_saxs" in items
    assert "qc" in items


def test_dialog_submits_via_client(qtbot):
    client = MagicMock()
    client.list_available.return_value = [
        {"name": "reduce_saxs", "description": "x", "parameters_schema": {}},
    ]
    dialog = RunPipelineDialog(client=client, run_uid="abc", input_access_blob={"tags": ["t"]})
    qtbot.addWidget(dialog)
    dialog.user_id = "rpandolfi"
    dialog._submit()
    client.submit.assert_called_once()
    args, kwargs = client.submit.call_args
    assert kwargs["pipeline"] == "reduce_saxs"
    assert kwargs["input_run_uid"] == "abc"


def test_dialog_disables_ok_when_no_pipelines(qtbot):
    client = MagicMock()
    client.list_available.return_value = []
    dialog = RunPipelineDialog(client=client, run_uid="abc", input_access_blob={})
    qtbot.addWidget(dialog)
    assert not dialog._ok_button.isEnabled()


def test_dialog_shows_error_on_submit_exception(qtbot, monkeypatch):
    client = MagicMock()
    client.list_available.return_value = [{"name": "p", "description": "", "parameters_schema": {}}]
    client.submit.side_effect = RuntimeError("executor offline")

    dialog = RunPipelineDialog(client=client, run_uid="abc", input_access_blob={})
    qtbot.addWidget(dialog)

    captured = {}
    def fake_critical(parent, title, text, *args, **kwargs):
        captured["title"] = title
        captured["text"] = text
        return None

    from lucid.ui.dialogs import run_pipeline_dialog as mod
    monkeypatch.setattr(mod.QMessageBox, "critical", fake_critical)

    dialog._submit()

    assert captured.get("title") == "Pipeline error"
    assert "executor offline" in captured.get("text", "")
    # Dialog must NOT have accepted (it's still open).
    from PySide6.QtWidgets import QDialog
    assert dialog.result() != QDialog.Accepted
