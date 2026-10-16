"""PreferencesManager fills identity prefs from site.bind when nothing sets them."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lightfall.ui.preferences.manager import PreferencesManager


class FakeCM:
    def __init__(self, bind, **prefs):
        self.model = SimpleNamespace(site=SimpleNamespace(facility="als", bind=bind))
        self.data = {f"preferences.{k}": v for k, v in prefs.items()}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, *, persist=False):
        self.data[key] = value


@pytest.fixture
def pm(qapp):
    PreferencesManager.reset()
    yield PreferencesManager.get_instance()
    PreferencesManager.reset()


def test_bound_console_derives_identity(pm):
    pm.set_config_manager(FakeCM("branch:7.0.1.1"))
    assert pm.get("tiled_beamline") == "7.0.1"
    assert pm.get("ipc_topic_prefix", "als.7011") == "als.7011"
    assert pm.get("current_beamline", "default") == "7.0.1"


def test_explicit_value_wins_and_blank_counts_as_unset(pm):
    pm.set_config_manager(FakeCM("branch:7.0.2.1", ipc_topic_prefix="dev.me", tiled_beamline=""))
    assert pm.get("ipc_topic_prefix", "als.7011") == "dev.me"
    assert pm.get("tiled_beamline", "") == "7.0.2"


def test_unbound_keeps_caller_default(pm):
    pm.set_config_manager(FakeCM(None))
    assert pm.get("ipc_topic_prefix", "als.7011") == "als.7011"
    assert pm.get("tiled_beamline", None) is None
