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
