import pytest
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication

from lightfall.monitor.registry import (
    DISABLED_MONITORS_PREF, MonitorRegistry,
)
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.ui.preferences.monitor_settings import (
    ADVISOR_ENABLED_PREF, TICK_INTERVAL_PREF, MonitorSettingsPlugin,
)


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def _prefs_with_cm(_app):
    """Provide a real PreferencesManager backed by a MagicMock ConfigManager
    so set/get round-trips work in tests (the default test env has no
    ConfigManager and LocalPreferenceBackend silently drops writes)."""
    store: dict = {}
    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: store.get(key, default)
    cm.set.side_effect = lambda key, value, persist=True: store.update({key: value})
    PreferencesManager.reset()
    prefs = PreferencesManager.get_instance()
    prefs.set_config_manager(cm)
    yield prefs
    PreferencesManager.reset()


def test_settings_roundtrip(_app, _prefs_with_cm):
    MonitorRegistry.reset_instance()
    plugin = MonitorSettingsPlugin()
    plugin.create_widget()
    plugin.load_settings()
    plugin._advisor_check.setChecked(True)
    plugin._interval_spin.setValue(45)
    plugin.save_settings()
    prefs = PreferencesManager.get_instance()
    assert prefs.get(ADVISOR_ENABLED_PREF) is True
    assert prefs.get(TICK_INTERVAL_PREF) == 45
    # cleanup
    prefs.remove(ADVISOR_ENABLED_PREF); prefs.remove(TICK_INTERVAL_PREF)
    MonitorRegistry.reset_instance()
