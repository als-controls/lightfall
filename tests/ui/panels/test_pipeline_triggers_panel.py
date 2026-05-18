"""Tests for the Pipeline Triggers settings panel."""
from unittest.mock import MagicMock

import pytest

from lucid.ui.panels.pipeline_triggers_panel import PipelineTriggersPanel


def test_panel_loads_existing_triggers(qtbot):
    backend = MagicMock()
    backend.load.return_value = [
        {"type": "run_end", "filter": {"plan_name": "count"},
         "pipeline": "reduce_saxs", "parameter_overrides": {}},
    ]
    manager = MagicMock()
    panel = PipelineTriggersPanel(manager=manager, settings_backend=backend)
    qtbot.addWidget(panel)
    assert panel.row_count() == 1


def test_panel_adds_new_trigger(qtbot):
    backend = MagicMock()
    backend.load.return_value = []
    manager = MagicMock()
    panel = PipelineTriggersPanel(manager=manager, settings_backend=backend)
    qtbot.addWidget(panel)
    panel.add_trigger({"type": "run_end", "filter": {"plan_name": "scan"},
                       "pipeline": "p", "parameter_overrides": {}})
    assert panel.row_count() == 1
    backend.save.assert_called()
    manager.add.assert_called()


def test_panel_rejects_unknown_trigger_type(qtbot):
    backend = MagicMock()
    backend.load.return_value = []
    manager = MagicMock()
    panel = PipelineTriggersPanel(manager=manager, settings_backend=backend)
    qtbot.addWidget(panel)
    with pytest.raises(ValueError, match="Unknown trigger type"):
        panel.add_trigger({"type": "bogus", "filter": {}, "pipeline": "p",
                           "parameter_overrides": {}})
    # No state change: row was not added, manager was not called for this one
    assert panel.row_count() == 0
    manager.add.assert_not_called()


def test_panel_no_phantom_row_when_manager_rejects(qtbot):
    backend = MagicMock()
    backend.load.return_value = []
    manager = MagicMock()
    manager.add.side_effect = RuntimeError("rejected")
    panel = PipelineTriggersPanel(manager=manager, settings_backend=backend)
    qtbot.addWidget(panel)
    with pytest.raises(RuntimeError, match="rejected"):
        panel.add_trigger({"type": "run_end", "filter": {}, "pipeline": "p",
                           "parameter_overrides": {}})
    assert panel.row_count() == 0


def test_panel_logs_when_save_fails(qtbot):
    backend = MagicMock()
    backend.load.return_value = []
    backend.save.side_effect = OSError("disk full")
    manager = MagicMock()
    panel = PipelineTriggersPanel(manager=manager, settings_backend=backend)
    qtbot.addWidget(panel)
    # add_trigger should NOT raise — save failure is caught
    panel.add_trigger({"type": "run_end", "filter": {}, "pipeline": "p",
                       "parameter_overrides": {}})
    assert panel.row_count() == 1
    manager.add.assert_called_once()
