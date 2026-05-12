"""Tests for the refactored PreferencesManager (multiplex + subscribe API)."""
from __future__ import annotations

import time
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
    cm = MagicMock()
    cm._store: dict = {}

    def _get(key, default=None):
        return cm._store.get(key, default)

    def _set(key, value, persist=True):
        cm._store[key] = value

    cm.get.side_effect = _get
    cm.set.side_effect = _set
    return cm


@pytest.fixture(autouse=True)
def _patch_user_settings_client(monkeypatch):
    """Replace UserSettingsClient.get_instance() with a MagicMock so the
    UserPortableBackend can be constructed without real HTTP."""
    from lucid.settings import user_settings_client as usc_mod

    fake = MagicMock()
    fake.set.return_value = None
    fake.delete.return_value = None
    fake.get_all.return_value = {}
    monkeypatch.setattr(
        usc_mod.UserSettingsClient,
        "get_instance",
        classmethod(lambda cls: fake),
    )
    return fake


@pytest.fixture
def prefs_manager(qapp, config_manager):
    from lucid.ui.preferences.manager import PreferencesManager
    PreferencesManager.reset()
    mgr = PreferencesManager(config_manager=config_manager)
    yield mgr
    PreferencesManager.reset()


def _flush(times: int = 5) -> None:
    for _ in range(times):
        QCoreApplication.processEvents()


def test_subscribe_routes_only_subscribed_key(qapp, prefs_manager):
    received: list = []
    prefs_manager.subscribe("theme", received.append)

    prefs_manager.set("theme", "dark")
    prefs_manager.set("font_size", 14)
    _flush()

    assert received == ["dark"]


def test_subscribe_multiple_slots_per_key(qapp, prefs_manager):
    received_a: list = []
    received_b: list = []
    prefs_manager.subscribe("theme", received_a.append)
    prefs_manager.subscribe("theme", received_b.append)

    prefs_manager.set("theme", "evangelion")
    _flush()

    assert received_a == ["evangelion"]
    assert received_b == ["evangelion"]


def test_unsubscribe_stops_delivery(qapp, prefs_manager):
    received: list = []
    prefs_manager.subscribe("theme", received.append)
    prefs_manager.unsubscribe("theme", received.append)

    prefs_manager.set("theme", "dark")
    _flush()

    assert received == []


def test_unsubscribe_for_unknown_key_is_noop(qapp, prefs_manager):
    # Should not raise.
    prefs_manager.unsubscribe("never_subscribed", lambda v: None)


def test_set_dispatches_to_local_backend_for_local_key(qapp, prefs_manager, config_manager):
    prefs_manager.set("theme", "dark")
    assert config_manager._store["preferences.theme"] == "dark"
    assert prefs_manager.get("theme") == "dark"


def test_user_portable_get_is_cache_only_initially(qapp, prefs_manager):
    """Without a refresh, user-portable get() returns the default."""
    assert prefs_manager.get("profile_image_id", default=None) is None


def test_user_portable_set_emits_through_topic(qapp, prefs_manager):
    received: list = []
    prefs_manager.subscribe("profile_image_id", received.append)

    prefs_manager.set("profile_image_id", "img-42")

    deadline = time.monotonic() + 2.0
    while not received and time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert received == ["img-42"]
    # And get() now returns the cached value.
    assert prefs_manager.get("profile_image_id") == "img-42"


def test_refresh_user_portable_keys_calls_backend_refresh(qapp, prefs_manager, _patch_user_settings_client):
    _patch_user_settings_client.get_all.return_value = {"profile_image_id": "img-7"}

    received: list = []
    prefs_manager.subscribe("profile_image_id", received.append)

    prefs_manager.refresh_user_portable_keys()
    deadline = time.monotonic() + 2.0
    while not received and time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert received == ["img-7"]
    assert prefs_manager.get("profile_image_id") == "img-7"
