"""Tests for the refactored PreferencesManager (multiplex + subscribe API)."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication


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
    from lightfall.settings import user_settings_client as usc_mod

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
    from lightfall.ui.preferences.manager import PreferencesManager
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


def test_bound_method_subscriber_auto_freed_when_owner_gc(prefs_manager):
    """A bound-method subscriber should not keep its owner alive."""
    import gc
    import weakref

    class Receiver:
        def __init__(self):
            self.received: list = []

        def on_change(self, value):
            self.received.append(value)

    r = Receiver()
    wref = weakref.ref(r)
    prefs_manager.subscribe("theme", r.on_change)

    # Sanity: first emit reaches the receiver.
    prefs_manager.set("theme", "dark")
    _flush()
    assert r.received == ["dark"]

    # Drop the strong ref + force GC.
    del r
    gc.collect()
    assert wref() is None, "Receiver was held alive by subscription"

    # Subsequent emits must not crash and must not keep growing the topic.
    prefs_manager.set("theme", "ev")
    _flush()
    topic = prefs_manager._topics["theme"]
    # _Topic prunes dead weakrefs on dispatch — should be empty now.
    assert len(topic._slots) == 0


def test_slot_exception_does_not_block_other_subscribers(prefs_manager):
    """If one slot raises, other subscribers still receive the value."""
    received_after_failing: list = []

    def failing_slot(value):
        raise RuntimeError("boom")

    prefs_manager.subscribe("theme", failing_slot)
    prefs_manager.subscribe("theme", received_after_failing.append)

    prefs_manager.set("theme", "dark")
    _flush()

    assert received_after_failing == ["dark"]


def test_lambda_subscriber_held_strongly(prefs_manager):
    """Lambdas have no caller-held reference; the manager must hold them."""
    received: list = []
    prefs_manager.subscribe("theme", lambda v: received.append(v))
    # No local reference to the lambda — would be GC'd if held weakly.

    prefs_manager.set("theme", "dark")
    _flush()

    assert received == ["dark"]


def test_unsubscribe_with_bound_method(qapp, prefs_manager):
    """unsubscribe on a bound method removes the right entry."""
    class Receiver:
        def __init__(self):
            self.received: list = []

        def on_change(self, value):
            self.received.append(value)

    r = Receiver()
    prefs_manager.subscribe("theme", r.on_change)
    prefs_manager.unsubscribe("theme", r.on_change)

    prefs_manager.set("theme", "dark")
    _flush()

    assert r.received == []


# ── User-portable with local fallback ──────────────────────────────────


def test_device_favorites_falls_back_to_local_when_user_portable_empty(
    qapp, prefs_manager, config_manager
):
    """A user-portable cache miss for device_favorites consults the local
    backend so site/beamline defaults populate new users."""
    config_manager._store["preferences.device_favorites"] = ["motor_a", "motor_b"]
    assert prefs_manager.get("device_favorites", []) == ["motor_a", "motor_b"]


def test_device_favorites_user_value_wins_over_local_default(
    qapp, prefs_manager, config_manager
):
    """Once the user-portable cache has a value (e.g., post-refresh or
    post-set), the local default no longer leaks through."""
    config_manager._store["preferences.device_favorites"] = ["motor_a"]
    prefs_manager.set("device_favorites", ["motor_x"])

    deadline = time.monotonic() + 2.0
    while prefs_manager._user_portable.get("device_favorites") != ["motor_x"] \
            and time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert prefs_manager.get("device_favorites") == ["motor_x"]


def test_device_favorites_explicit_empty_user_value_overrides_default(
    qapp, prefs_manager, config_manager
):
    """An explicitly-saved empty list means "user wants no favorites" and
    must NOT be substituted with the local default."""
    config_manager._store["preferences.device_favorites"] = ["motor_a"]
    prefs_manager.set("device_favorites", [])

    deadline = time.monotonic() + 2.0
    while prefs_manager._user_portable.get("device_favorites", "missing") == "missing" \
            and time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert prefs_manager.get("device_favorites") == []


def test_device_favorites_beamline_override_visible_through_fallback(
    qapp, prefs_manager, config_manager
):
    """When a beamline override exists in local config and the user has
    no server value, the beamline-scoped default reaches callers."""
    config_manager._store["preferences.device_favorites"] = ["default_motor"]
    config_manager._store["preferences.beamlines.7.0.1.device_favorites"] = ["bl_motor"]
    prefs_manager.set_beamline("7.0.1")

    assert prefs_manager.get("device_favorites", []) == ["bl_motor"]


# ── Sensitive-value redaction ──────────────────────────────────────────


def test_set_redacts_sensitive_values_in_logs(qapp, prefs_manager):
    """Credential-bearing keys must never reach the logs in cleartext
    (the app runs at DEBUG, so every set() is logged)."""
    from lightfall.utils.logging import logger

    messages: list[str] = []
    sink_id = logger.add(messages.append, level="DEBUG")
    try:
        prefs_manager.set("claude_api_key", "sk-ant-supersecret")
        prefs_manager.set("theme", "dark")
    finally:
        logger.remove(sink_id)

    joined = "\n".join(messages)
    assert "sk-ant-supersecret" not in joined
    assert "claude_api_key = <redacted>" in joined
    # Non-sensitive values still log in cleartext.
    assert "theme = dark" in joined


@pytest.mark.parametrize(
    "key",
    ["tiled_api_key", "auth_token", "client_secret", "db_password", "telemetry_dsn"],
)
def test_loggable_value_redacts_marker_keys(key):
    from lightfall.ui.preferences.manager import _loggable_value

    assert _loggable_value(key, "hunter2") == "<redacted>"


def test_loggable_value_passes_plain_keys():
    from lightfall.ui.preferences.manager import _loggable_value

    assert _loggable_value("font_size", 14) == 14


def test_device_favorites_subscribe_gets_local_fallback_on_user_remove(
    qapp, prefs_manager, config_manager, _patch_user_settings_client
):
    """When user-portable removes a key with local fallback, subscribers
    receive the local fallback value, not None."""
    config_manager._store["preferences.device_favorites"] = ["fallback_motor"]
    # Seed the user-portable cache so remove() has something to clear.
    prefs_manager.set("device_favorites", ["user_motor"])
    deadline = time.monotonic() + 2.0
    while prefs_manager._user_portable.get("device_favorites") != ["user_motor"] \
            and time.monotonic() < deadline:
        QCoreApplication.processEvents()

    received: list = []
    prefs_manager.subscribe("device_favorites", received.append)
    prefs_manager.remove("device_favorites")

    deadline = time.monotonic() + 2.0
    while not received and time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert received == [["fallback_motor"]]
