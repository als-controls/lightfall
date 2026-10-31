"""Settings pages and startup read the same Tiled, logbook and IPC defaults (spec section 6)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _EmptyPrefs:
    """Returns the caller's default for every key and records which default was asked for."""

    def __init__(self):
        self.asked: dict[str, object] = {}

    def get(self, key, default=None):
        self.asked[key] = default
        return default

    def set(self, key, value):
        pass

    def derived_value(self, key):
        return None  # an unbound console: nothing derives from site.bind

    def explicit(self, key, default=None):
        return self.get(key, default)

    def subscribe(self, key, slot):
        pass  # startup's watch_tiled_preferences: nothing changes here


@pytest.fixture
def empty_prefs(monkeypatch):
    from lightfall.ui.preferences.manager import PreferencesManager

    prefs = _EmptyPrefs()
    monkeypatch.setattr(PreferencesManager, "get_instance", classmethod(lambda cls: prefs))
    return prefs


def test_tiled_defaults_are_off_and_shared(qapp, empty_prefs):
    from lightfall.services.tiled_service import DEFAULT_TILED_URL, TILED_DEFAULTS
    from lightfall.ui.preferences.tiled_settings import TiledSettingsPlugin

    assert TILED_DEFAULTS == {"tiled_enabled": False, "tiled_url": "", "tiled_auth_mode": ""}
    page = TiledSettingsPlugin()
    page.create_widget()
    page.load_settings()
    assert page._enabled_check.isChecked() is False
    assert page._url_edit.text() == ""
    assert page._url_edit.placeholderText() == DEFAULT_TILED_URL
    assert page._auth_mode_combo.currentData() == ""
    for key, value in TILED_DEFAULTS.items():
        assert empty_prefs.asked[key] == value


def test_tiled_startup_uses_the_same_defaults(qapp, empty_prefs):
    from lightfall import main as main_mod
    from lightfall.services.tiled_service import TILED_DEFAULTS, TiledService

    TiledService.reset()
    try:
        config = MagicMock()
        config.model.auth.provider.type = "local"
        main_mod._setup_tiled(MagicMock(), config)
        for key, value in TILED_DEFAULTS.items():
            assert empty_prefs.asked[key] == value
        assert TiledService.get_instance().config.enabled is False
    finally:
        TiledService.reset()


def test_logbook_page_and_client_share_the_default(qapp, empty_prefs):
    from lightfall.logbook.url import DEFAULT_LOGBOOK_URL, get_logbook_base_url
    from lightfall.ui.preferences.logbook_settings import LogbookSettingsPlugin

    assert DEFAULT_LOGBOOK_URL == "http://bcglucidlogbook.dhcp.lbl.gov:8000"
    page = LogbookSettingsPlugin()
    page.create_widget()
    page.load_settings()
    assert empty_prefs.asked["logbook_url"] == DEFAULT_LOGBOOK_URL
    assert page._url_edit.text() == DEFAULT_LOGBOOK_URL
    assert page._url_edit.placeholderText() == DEFAULT_LOGBOOK_URL
    assert get_logbook_base_url() == DEFAULT_LOGBOOK_URL


def test_ipc_page_and_startup_share_the_defaults(qapp, empty_prefs):
    from lightfall.core.application import LFApplication
    from lightfall.ipc.defaults import DEFAULT_IPC_NATS_URL, DEFAULT_IPC_TOPIC_PREFIX
    from lightfall.ui.preferences.ipc_settings import IPCSettingsPlugin

    assert DEFAULT_IPC_TOPIC_PREFIX == "als.7011"
    page = IPCSettingsPlugin()
    page.create_widget()
    page.load_settings()
    assert page._prefix_edit.text() == DEFAULT_IPC_TOPIC_PREFIX
    assert page._url_edit.text() == DEFAULT_IPC_NATS_URL

    app = LFApplication.__new__(LFApplication)
    svc = app._create_ipc_service(MagicMock())
    assert empty_prefs.asked["ipc_topic_prefix"] == DEFAULT_IPC_TOPIC_PREFIX
    assert svc.nats_url == DEFAULT_IPC_NATS_URL
    assert svc._topic_prefix == DEFAULT_IPC_TOPIC_PREFIX
