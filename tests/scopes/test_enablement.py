"""lightfall.scopes.enablement: the resolved {id: bool} map, else the code default."""
from __future__ import annotations

import pytest

from lightfall.scopes.enablement import enabled_overrides, item_enabled
from lightfall.ui.preferences.manager import PreferencesManager
from tests.ui.preferences.test_manager_identity import FakeCM

KEY = "plugins.enabled"


@pytest.fixture
def pm(qapp):
    PreferencesManager.reset()
    yield PreferencesManager.get_instance()
    PreferencesManager.reset()


def test_item_enabled_default_and_override(pm):
    pm.set_config_manager(FakeCM(None, **{KEY: {"a": False, "b": True, "n": "x"}}))
    assert item_enabled(KEY, "a", True) is False
    assert item_enabled(KEY, "b", False) is True
    assert item_enabled(KEY, "missing", True) is True
    assert item_enabled(KEY, "missing", False) is False
    assert item_enabled(KEY, "n", True) is True  # a non-bool entry is not an answer


def test_enabled_overrides_keeps_only_bools(pm):
    pm.set_config_manager(FakeCM(None, **{KEY: {"a": False, "b": True, "n": 1, "s": "x"}}))
    assert enabled_overrides(KEY) == {"a": False, "b": True}


def test_enablement_without_prefs_returns_default(monkeypatch):
    def boom():
        raise RuntimeError("no preferences yet")

    monkeypatch.setattr(PreferencesManager, "get_instance", staticmethod(boom))
    assert item_enabled(KEY, "a", True) is True
    assert item_enabled(KEY, "a", False) is False
    assert enabled_overrides(KEY) == {}


def test_enablement_import_is_light():
    import subprocess
    import sys

    code = ("import sys, lightfall.scopes.enablement;"
            "assert 'PySide6' not in sys.modules and "
            "'lightfall.ui.preferences.manager' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], check=True)
