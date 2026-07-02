import pytest
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication

from lightfall.monitor.monitor_plugin import MonitorPlugin
from lightfall.monitor.registry import (
    DISABLED_MONITORS_PREF, FORCED_ENABLED_MONITORS_PREF, MonitorRegistry,
)
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.ui.preferences.monitor_settings import (
    ADVISOR_ENABLED_PREF, TICK_INTERVAL_PREF, MonitorPluginTableModel,
    MonitorSettingsPlugin,
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


# ---------------------------------------------------------------------------
# NEW: MonitorPluginTableModel — enable/disable round-trip
# ---------------------------------------------------------------------------

class _FakePlugin(MonitorPlugin):
    """Minimal concrete MonitorPlugin for testing the table model."""

    def __init__(self, name: str, *, enabled_by_default: bool = True):
        self._name = name
        self._enabled_by_default = enabled_by_default

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Fake plugin {self._name}"

    @property
    def enabled_by_default(self) -> bool:
        return self._enabled_by_default

    def create_feeds(self):
        return []


@pytest.fixture
def _clean_registry():
    """Provide an empty MonitorRegistry and reset it after the test."""
    MonitorRegistry.reset_instance()
    reg = MonitorRegistry.get_instance()
    yield reg
    MonitorRegistry.reset_instance()


def test_table_model_enable_disable_roundtrip(_app, _clean_registry):
    """register two plugins (one enabled-by-default, one disabled-by-default),
    then drive set_overrides/get_overrides through various toggle states and
    confirm the round-trip is correct."""
    reg = _clean_registry
    alpha = _FakePlugin("alpha", enabled_by_default=True)
    beta  = _FakePlugin("beta",  enabled_by_default=False)
    reg.register(alpha)
    reg.register(beta)

    model = MonitorPluginTableModel()
    model.refresh()

    assert model.rowCount() == 2

    # --- baseline: no overrides ---
    model.set_overrides(disabled_names=set(), forced_enabled_names=set())
    disabled, forced = model.get_overrides()
    # alpha is on by default and not toggled -> no overrides
    assert "alpha" not in disabled
    assert "alpha" not in forced
    # beta is off by default and not forced -> no overrides
    assert "beta" not in disabled
    assert "beta" not in forced

    # --- disable alpha (was on by default) ---
    model.set_overrides(disabled_names={"alpha"}, forced_enabled_names=set())
    disabled, forced = model.get_overrides()
    assert "alpha" in disabled
    assert "beta"  not in forced  # beta still off-by-default, not forced

    # --- force-enable beta (was off by default) ---
    model.set_overrides(disabled_names=set(), forced_enabled_names={"beta"})
    disabled, forced = model.get_overrides()
    assert "alpha" not in disabled
    assert "beta"  in forced

    # --- simultaneous: disable alpha AND force beta ---
    model.set_overrides(disabled_names={"alpha"}, forced_enabled_names={"beta"})
    disabled, forced = model.get_overrides()
    assert "alpha" in disabled
    assert "beta"  in forced
