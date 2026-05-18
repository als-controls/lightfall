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
