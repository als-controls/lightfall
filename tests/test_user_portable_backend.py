"""Tests for UserPortableBackend.

Uses MagicMock for UserSettingsClient so we can drive set/get/delete
synchronously while the backend's own QThreadFuture handles concurrency.
We block on the `changed` signal via QSignalSpy.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy


def _wait_for_spy(spy: QSignalSpy, expected_count: int, timeout_ms: int = 2000) -> None:
    """Pump the event loop until spy has at least `expected_count` items
    or `timeout_ms` elapses. Raises AssertionError on timeout."""
    import time
    from PySide6.QtCore import QCoreApplication
    deadline = time.monotonic() + timeout_ms / 1000.0
    while spy.count() < expected_count and time.monotonic() < deadline:
        QCoreApplication.processEvents()
    assert spy.count() >= expected_count, (
        f"expected {expected_count} signal(s), got {spy.count()}"
    )


def test_owns_only_user_portable_keys(qapp):
    from lightfall.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    b = UserPortableBackend(client)
    assert b.owns("profile_image_id") is True
    assert b.owns("theme") is False


def test_get_returns_default_when_cache_empty(qapp):
    from lightfall.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    b = UserPortableBackend(client)
    assert b.get("profile_image_id", default=None) is None
    assert b.get("profile_image_id", default="x") == "x"


def test_set_runs_async_and_emits_changed(qapp):
    from lightfall.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    client.set.return_value = None  # successful PUT

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.set("profile_image_id", "img-1")
    _wait_for_spy(spy, 1)

    assert spy.at(0)[0] == "profile_image_id"
    assert spy.at(0)[1] == "img-1"
    assert b.get("profile_image_id") == "img-1"
    client.set.assert_called_once_with("profile_image_id", "img-1")


def test_set_failure_leaves_cache_untouched_and_no_emit(qapp):
    from lightfall.ui.preferences.user_portable_backend import UserPortableBackend
    from lightfall.settings.user_settings_client import UserSettingsError

    client = MagicMock()
    client.set.side_effect = UserSettingsError("boom")

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)
    b.set("profile_image_id", "img-1")

    # Drain the loop a little; should NOT see a signal.
    import time
    from PySide6.QtCore import QCoreApplication
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert spy.count() == 0
    assert b.get("profile_image_id") is None


def test_remove_emits_none_and_clears_cache(qapp):
    from lightfall.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    client.set.return_value = None
    client.delete.return_value = None

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.set("profile_image_id", "img-1")
    _wait_for_spy(spy, 1)
    b.remove("profile_image_id")
    _wait_for_spy(spy, 2)

    assert spy.at(1)[0] == "profile_image_id"
    assert spy.at(1)[1] is None
    assert b.get("profile_image_id") is None


def test_refresh_populates_cache_and_emits_per_changed_key(qapp):
    from lightfall.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    client.get_all.return_value = {"profile_image_id": "img-7"}

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.refresh()
    _wait_for_spy(spy, 1)

    assert spy.at(0)[0] == "profile_image_id"
    assert spy.at(0)[1] == "img-7"
    assert b.get("profile_image_id") == "img-7"


def test_refresh_emits_none_for_removed_keys(qapp):
    """Server has no profile_image_id but cache had one — emit (key, None)."""
    from lightfall.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()

    # Seed: server responds with the key, then drops it on second refresh.
    client.get_all.side_effect = [
        {"profile_image_id": "img-1"},
        {},  # disappeared
    ]
    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.refresh()
    _wait_for_spy(spy, 1)
    b.refresh()
    _wait_for_spy(spy, 2)

    assert spy.at(1)[0] == "profile_image_id"
    assert spy.at(1)[1] is None
    assert b.get("profile_image_id") is None


def test_refresh_skips_in_flight_set(qapp):
    """If a key has an in-flight set, refresh must not clobber it."""
    from lightfall.ui.preferences.user_portable_backend import UserPortableBackend
    import time

    client = MagicMock()
    # Make set() slow so we can fire refresh() while it's still running.
    def slow_set(key, value):
        time.sleep(0.3)
    client.set.side_effect = slow_set
    client.get_all.return_value = {"profile_image_id": "stale-server-value"}

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.set("profile_image_id", "new-value")
    # While slow_set is still running on the worker thread, fire refresh.
    b.refresh()

    # Wait for both futures to complete (set + refresh).
    _wait_for_spy(spy, 1, timeout_ms=3000)

    # After everything settles, the cache must equal the value we wrote,
    # not the stale value the server returned during refresh.
    assert b.get("profile_image_id") == "new-value"
