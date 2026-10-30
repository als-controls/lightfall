"""Visualization and Login & Session pages: the keys they write at the user scope."""

from __future__ import annotations

import pytest

from lightfall.ui.preferences.login_settings import LoginSettingsPlugin
from lightfall.ui.preferences.visualization_settings import VisualizationSettingsPlugin


class RecordingPrefs:
    def __init__(self):
        self.sets: dict = {}

    def get(self, key, default=None):
        return default

    def set(self, key, value, **_kw):
        self.sets[key] = value


@pytest.fixture
def prefs(monkeypatch):
    p = RecordingPrefs()
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: p)
    )
    return p


def test_visualization_user_scope_writes_same_keys(qtbot, prefs):
    plugin = VisualizationSettingsPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    plugin.load_settings()
    plugin._auto_select.setChecked(False)
    plugin._default_viz.setCurrentText("heatmap")
    plugin._decimation_threshold.setValue(5000)
    plugin._update_rate.setValue(30)
    plugin._default_colormap.setCurrentText("magma")
    plugin._show_grid.setChecked(False)
    plugin._show_legend.setChecked(False)
    plugin.save_settings()
    assert prefs.sets == {
        "visualization.auto_select": False,
        "visualization.default_type": "heatmap",
        "visualization.decimation_threshold": 5000,
        "visualization.update_rate": 30,
        "visualization.colormap": "magma",
        "visualization.show_grid": False,
        "visualization.show_legend": False,
    }


def test_login_user_scope_writes_same_key(qtbot, prefs):
    plugin = LoginSettingsPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    plugin.load_settings()
    plugin._duration_combo.setCurrentIndex(plugin._duration_combo.findData(28800))
    plugin.save_settings()
    assert prefs.sets == {"session_duration": 28800}
