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
    from types import SimpleNamespace

    from lightfall.services.tiled_service import DEFAULT_TILED_URL, TILED_DEFAULTS
    from lightfall.ui.preferences import tiled_settings
    from lightfall.ui.preferences.form.schema import section_fields

    assert TILED_DEFAULTS == {"tiled_enabled": False, "tiled_url": "", "tiled_auth_mode": ""}
    fields = {f.key: f for s in tiled_settings.TiledSettingsPlugin().sections() for f in section_fields(s)}
    assert fields["tiled_url"].placeholder == DEFAULT_TILED_URL
    # The page's predicates and Test Connection fall back to the same defaults.
    asked: dict[str, object] = {}

    def effective(key, default=None):
        asked[key] = default
        return default

    values = SimpleNamespace(scope="local", effective=effective, get=lambda key: None, is_dirty=lambda key: False)
    assert fields["tiled_url"].enabled_when(values) is False
    assert fields["tiled_api_key"].visible_when(values) is False
    with pytest.raises(RuntimeError, match="Enter a URL first"):
        tiled_settings._test_connection(values)
    for key, value in TILED_DEFAULTS.items():
        assert asked[key] == value


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


def test_logbook_page_and_client_share_the_default(qapp):
    from lightfall.logbook.url import DEFAULT_LOGBOOK_URL, get_logbook_base_url
    from lightfall.ui.preferences.form.schema import SettingField
    from lightfall.ui.preferences.logbook_settings import (
        LogbookSettingsPlugin,
        _test_connection,
    )

    assert DEFAULT_LOGBOOK_URL == "http://bcglucidlogbook.dhcp.lbl.gov:8000"
    fields = {
        item.key: item
        for section in LogbookSettingsPlugin().sections()
        for item in section.items
        if isinstance(item, SettingField)
    }
    assert fields["logbook_url"].placeholder == DEFAULT_LOGBOOK_URL
    assert get_logbook_base_url() == DEFAULT_LOGBOOK_URL

    asked: dict[str, object] = {}

    class _Values:
        def effective(self, key, default=None):
            asked[key] = default
            return default

    from unittest.mock import patch

    import httpx

    with patch.object(httpx, "Client", side_effect=RuntimeError("stop")):
        with pytest.raises(RuntimeError):
            _test_connection(_Values())
    assert asked["logbook_url"] == DEFAULT_LOGBOOK_URL


def test_ipc_page_and_startup_share_the_defaults(qapp, empty_prefs):
    from lightfall.core.application import LFApplication
    from lightfall.ipc.defaults import DEFAULT_IPC_NATS_URL, DEFAULT_IPC_TOPIC_PREFIX
    from lightfall.ui.preferences.ipc_settings import IPCSettingsPlugin

    assert DEFAULT_IPC_TOPIC_PREFIX == "als.7011"
    from lightfall.ui.preferences.form.schema import section_fields

    fields = {f.key: f for s in IPCSettingsPlugin().sections() for f in section_fields(s)}
    assert fields["ipc_topic_prefix"].placeholder == DEFAULT_IPC_TOPIC_PREFIX
    assert fields["ipc_nats_url"].placeholder == DEFAULT_IPC_NATS_URL

    app = LFApplication.__new__(LFApplication)
    svc = app._create_ipc_service(MagicMock())
    assert empty_prefs.asked["ipc_topic_prefix"] == DEFAULT_IPC_TOPIC_PREFIX
    assert svc.nats_url == DEFAULT_IPC_NATS_URL
    assert svc._topic_prefix == DEFAULT_IPC_TOPIC_PREFIX
