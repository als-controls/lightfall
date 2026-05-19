"""Tests for the Pipeline Triggers settings panel."""
from unittest.mock import MagicMock

import pytest

from lucid.ui.panels.pipeline_triggers_panel import (
    PipelineTriggersDockPanel,
    PipelineTriggersPanel,
    _PreferencesTriggerBackend,
)


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


def test_preferences_backend_round_trips_specs():
    """The thin PreferencesManager adapter stores and reads back a spec list."""
    store = {}
    prefs = MagicMock()
    prefs.get.side_effect = lambda k, d=None: store.get(k, d)
    prefs.set.side_effect = lambda k, v: store.update({k: v})

    backend = _PreferencesTriggerBackend(prefs=prefs, key="pipeline_triggers")
    assert backend.load() == []
    specs = [{"type": "run_end", "pipeline": "p", "filter": {}, "parameter_overrides": {}}]
    backend.save("pipeline_triggers", specs)
    assert backend.load() == specs


def test_preferences_backend_returns_empty_list_when_value_not_list():
    """Defends against a corrupted pref value containing something non-list."""
    prefs = MagicMock()
    prefs.get.return_value = "not a list"
    backend = _PreferencesTriggerBackend(prefs=prefs, key="pipeline_triggers")
    assert backend.load() == []


def test_dock_panel_embeds_inner_when_manager_registered(qtbot, monkeypatch):
    """DockPanel pulls TriggerManager from registry and constructs an inner panel."""
    from lucid.acquire.triggers.manager import TriggerManager
    from lucid.core.services import ServiceRegistry
    from lucid.ui.preferences import PreferencesManager

    manager = MagicMock()
    prefs = MagicMock()
    prefs.get.return_value = []
    registry = ServiceRegistry.get_instance()

    def fake_get(key, default=None):
        if key is TriggerManager:
            return manager
        if key is PreferencesManager:
            return prefs
        return default

    monkeypatch.setattr(registry, "get", fake_get)

    panel = PipelineTriggersDockPanel()
    qtbot.addWidget(panel)
    assert hasattr(panel, "_inner")


def test_dock_panel_shows_placeholder_when_no_manager(qtbot, monkeypatch):
    """No registered TriggerManager renders a placeholder label."""
    from lucid.core.services import ServiceRegistry

    registry = ServiceRegistry.get_instance()
    monkeypatch.setattr(registry, "get", lambda key, default=None: default)
    panel = PipelineTriggersDockPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "_inner")
