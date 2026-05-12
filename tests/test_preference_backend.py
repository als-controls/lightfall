"""Tests for PreferenceBackend ABC + LocalPreferenceBackend."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication


@pytest.fixture
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


@pytest.fixture
def config_manager():
    """A MagicMock-backed ConfigManager that records get/set calls."""
    cm = MagicMock()
    cm._store: dict = {}

    def _get(key, default=None):
        return cm._store.get(key, default)

    def _set(key, value, persist=True):
        cm._store[key] = value

    cm.get.side_effect = _get
    cm.set.side_effect = _set
    return cm


def test_abc_cannot_be_instantiated(qapp):
    from lucid.ui.preferences.backend import PreferenceBackend
    with pytest.raises(TypeError):
        PreferenceBackend()


def test_local_backend_owns_non_portable_key(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    b = LocalPreferenceBackend(config_manager)
    assert b.owns("theme") is True
    assert b.owns("font_size") is True


def test_local_backend_does_not_own_user_portable_key(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    b = LocalPreferenceBackend(config_manager)
    assert b.owns("profile_image_id") is False


def test_local_backend_get_returns_stored_value(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    config_manager._store["preferences.theme"] = "dark"
    b = LocalPreferenceBackend(config_manager)
    assert b.get("theme") == "dark"


def test_local_backend_get_returns_default_when_missing(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    b = LocalPreferenceBackend(config_manager)
    assert b.get("missing", default="fallback") == "fallback"


def test_local_backend_set_emits_changed(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    b = LocalPreferenceBackend(config_manager)
    captured: list[tuple] = []
    b.changed.connect(lambda k, v: captured.append((k, v)))

    b.set("theme", "evangelion")

    assert config_manager._store["preferences.theme"] == "evangelion"
    assert captured == [("theme", "evangelion")]


def test_local_backend_remove_emits_none(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    config_manager._store["preferences.theme"] = "dark"
    b = LocalPreferenceBackend(config_manager)
    captured: list[tuple] = []
    b.changed.connect(lambda k, v: captured.append((k, v)))

    b.remove("theme")

    assert config_manager._store["preferences.theme"] is None
    assert captured == [("theme", None)]


def test_local_backend_beamline_override_consulted_first(qapp, config_manager):
    """Beamline-specific keys (e.g., default_data_dir) read beamline first, fall back to global."""
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    config_manager._store["preferences.default_data_dir"] = "/data/global"
    config_manager._store["preferences.beamlines.7011.default_data_dir"] = "/data/7011"
    b = LocalPreferenceBackend(config_manager, beamline="7011")
    assert b.get("default_data_dir") == "/data/7011"


def test_local_backend_falls_back_when_no_beamline_override(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    config_manager._store["preferences.default_data_dir"] = "/data/global"
    b = LocalPreferenceBackend(config_manager, beamline="7011")
    assert b.get("default_data_dir") == "/data/global"
