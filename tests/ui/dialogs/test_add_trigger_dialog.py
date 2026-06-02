"""Tests for AddTriggerDialog."""
from PySide6.QtWidgets import QDialog

import pytest

from lightfall.ui.dialogs.add_trigger_dialog import AddTriggerDialog


def test_dialog_renders_lineedit_when_no_pipelines(qtbot):
    dialog = AddTriggerDialog()
    qtbot.addWidget(dialog)
    assert dialog._pipeline_combo is None
    assert dialog._pipeline_edit is not None


def test_dialog_renders_combo_when_pipelines_provided(qtbot):
    dialog = AddTriggerDialog(pipelines=["reduce_saxs", "qc"])
    qtbot.addWidget(dialog)
    assert dialog._pipeline_combo is not None
    items = [
        dialog._pipeline_combo.itemText(i)
        for i in range(dialog._pipeline_combo.count())
    ]
    assert items == ["reduce_saxs", "qc"]


def test_dialog_builds_full_spec(qtbot):
    dialog = AddTriggerDialog()
    qtbot.addWidget(dialog)
    dialog._type_combo.setCurrentText("run_end")
    dialog._pipeline_edit.setText("reduce_saxs")
    dialog._plan_name_edit.setText("count")
    dialog._tags_edit.setText("saxs, 2d")
    dialog._param_overrides_edit.setPlainText('{"roi_x": [0, 1024]}')
    dialog._on_accept()

    spec = dialog.spec()
    assert spec == {
        "type": "run_end",
        "filter": {"plan_name": "count", "tags_includes": ["saxs", "2d"]},
        "pipeline": "reduce_saxs",
        "parameter_overrides": {"roi_x": [0, 1024]},
    }
    assert dialog.result() == QDialog.Accepted


def test_dialog_drops_empty_filter_fields(qtbot):
    """Match-all filter must produce an empty dict, not {plan_name: ''}."""
    dialog = AddTriggerDialog()
    qtbot.addWidget(dialog)
    dialog._pipeline_edit.setText("p")
    dialog._on_accept()

    spec = dialog.spec()
    assert spec is not None
    assert spec["filter"] == {}
    assert spec["parameter_overrides"] == {}


def test_dialog_rejects_empty_pipeline(qtbot, monkeypatch):
    """Empty pipeline name fails validation; dialog stays open."""
    dialog = AddTriggerDialog()
    qtbot.addWidget(dialog)

    captured = {}
    def fake_warning(parent, title, text, *args, **kwargs):
        captured["title"] = title
    from lightfall.ui.dialogs import add_trigger_dialog as mod
    monkeypatch.setattr(mod.QMessageBox, "warning", fake_warning)

    dialog._on_accept()
    assert dialog.spec() is None
    assert dialog.result() != QDialog.Accepted
    assert captured["title"] == "Invalid trigger"


def test_dialog_rejects_invalid_json_overrides(qtbot, monkeypatch):
    dialog = AddTriggerDialog()
    qtbot.addWidget(dialog)
    dialog._pipeline_edit.setText("p")
    dialog._param_overrides_edit.setPlainText("not-json")

    captured = {}
    def fake_warning(parent, title, text, *args, **kwargs):
        captured["text"] = text
    from lightfall.ui.dialogs import add_trigger_dialog as mod
    monkeypatch.setattr(mod.QMessageBox, "warning", fake_warning)

    dialog._on_accept()
    assert dialog.spec() is None
    assert "JSON" in captured["text"]


def test_dialog_rejects_non_object_overrides(qtbot, monkeypatch):
    """JSON lists/scalars are syntactically valid but semantically wrong here."""
    dialog = AddTriggerDialog()
    qtbot.addWidget(dialog)
    dialog._pipeline_edit.setText("p")
    dialog._param_overrides_edit.setPlainText("[1, 2, 3]")

    captured = {}
    def fake_warning(parent, title, text, *args, **kwargs):
        captured["text"] = text
    from lightfall.ui.dialogs import add_trigger_dialog as mod
    monkeypatch.setattr(mod.QMessageBox, "warning", fake_warning)

    dialog._on_accept()
    assert dialog.spec() is None
    assert "JSON object" in captured["text"]
