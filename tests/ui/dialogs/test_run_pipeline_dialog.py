"""Tests for the Run Pipeline dialog."""
from unittest.mock import MagicMock

import pytest

from lightfall.ui.dialogs.run_pipeline_dialog import RunPipelineDialog


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
    client.submit.return_value = "job-xyz"
    dialog = RunPipelineDialog(client=client, run_uid="abc", input_access_blob={"tags": ["t"]})
    qtbot.addWidget(dialog)
    dialog.user_id = "rpandolfi"
    dialog._submit()
    # _submit kicks off a QThreadFuture; wait for the call to land.
    qtbot.waitUntil(lambda: client.submit.called, timeout=3000)
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

    from lightfall.ui.dialogs import run_pipeline_dialog as mod
    monkeypatch.setattr(mod.QMessageBox, "critical", fake_critical)

    dialog._submit()
    # except_slot runs on the main thread after the worker exception;
    # wait for QMessageBox.critical to be invoked.
    qtbot.waitUntil(lambda: "title" in captured, timeout=3000)
    assert captured["title"] == "Pipeline error"
    assert "executor offline" in captured["text"]
    # Dialog must NOT have accepted (it's still open).
    from PySide6.QtWidgets import QDialog
    assert dialog.result() != QDialog.Accepted
    # OK button is re-enabled so the user can retry.
    assert dialog._ok_button.isEnabled()


def test_dialog_disables_ok_button_during_submit(qtbot):
    """During an in-flight submit, the OK button must be disabled to
    prevent rapid double-clicks from spawning parallel futures.
    """
    import threading
    client = MagicMock()
    client.list_available.return_value = [
        {"name": "p", "description": "", "parameters_schema": {}},
    ]
    release = threading.Event()

    def slow_submit(**_kwargs):
        # Block the worker thread until the test releases it, so we can
        # observe the in-flight UI state.
        release.wait(timeout=2.0)
        return "job-xyz"

    client.submit.side_effect = slow_submit

    dialog = RunPipelineDialog(client=client, run_uid="abc", input_access_blob={})
    qtbot.addWidget(dialog)
    assert dialog._ok_button.isEnabled()

    dialog._submit()
    # While the worker is blocked: OK disabled, status reflects pending state.
    qtbot.waitUntil(lambda: not dialog._ok_button.isEnabled(), timeout=2000)
    assert dialog._status_label.text() == "Submitting..."

    # Release the worker; dialog should accept on success.
    release.set()
    qtbot.waitUntil(lambda: dialog.result() == 1, timeout=3000)  # QDialog.Accepted == 1
    assert dialog._status_label.text() == ""


def test_dialog_ignores_second_submit_while_first_in_flight(qtbot):
    """Guard against double-fire when the OK button somehow re-triggers
    before the slots have run."""
    import threading
    client = MagicMock()
    client.list_available.return_value = [
        {"name": "p", "description": "", "parameters_schema": {}},
    ]
    release = threading.Event()
    call_count = {"n": 0}

    def slow_submit(**_kwargs):
        call_count["n"] += 1
        release.wait(timeout=2.0)
        return "job-xyz"

    client.submit.side_effect = slow_submit

    dialog = RunPipelineDialog(client=client, run_uid="abc", input_access_blob={})
    qtbot.addWidget(dialog)
    dialog._submit()
    dialog._submit()  # Second call should be a no-op while first is running.
    release.set()
    qtbot.waitUntil(lambda: dialog.result() == 1, timeout=3000)
    assert call_count["n"] == 1
