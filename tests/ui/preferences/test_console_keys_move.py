"""Console keys an old page stored in the user layer move to This console (once)."""
from __future__ import annotations

import pytest

from lightfall.scopes import keys
from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.journal import WriteJournal
from lightfall.ui.preferences.scoped_backend import ScopedBackend
from tests.ui.preferences.test_scoped_backend import FakeLocal, Server, sync_run

KEY = "voice.daemon_host"


@pytest.fixture(autouse=True)
def _isolated_keys():
    keys._reset_client_registry()
    yield
    keys._reset_client_registry()


@pytest.fixture
def make(tmp_path, qapp):
    def _make(server, local):
        return ScopedBackend(
            local, client=server, cache=SnapshotCache(tmp_path / "cache"),
            journal=WriteJournal(tmp_path / "journal.json"), bind="beamline:7.0.1",
            identity="u1", user_preferences=dict, run=sync_run,
            notify=lambda t, m: None, poll_ms=0)
    return _make


def _server_with_user_value(value="h"):
    server = Server()
    server.rows[("user:u1", KEY)] = (value, 1)
    return server


def test_user_layer_value_moves_to_this_console(make):
    keys.register_console_keys([KEY])
    server, local = _server_with_user_value(), FakeLocal()
    backend = make(server, local)
    changes = []
    backend.changed.connect(lambda k, v: changes.append((k, v)))
    backend.activate()
    assert local.get(KEY) == "h"
    assert (KEY, "h") in changes
    assert server.rows[("user:u1", KEY)] == ("h", 1)  # server data untouched
    assert not [c for c in server.calls if c[2] == KEY]  # nothing written or deleted


def test_this_console_value_is_left_alone(make):
    keys.register_console_keys([KEY])
    server, local = _server_with_user_value(), FakeLocal(**{KEY: "mine"})
    make(server, local).activate()
    assert local.get(KEY) == "mine"


def test_move_is_idempotent_and_happens_once(make):
    keys.register_console_keys([KEY])
    server, local = _server_with_user_value(), FakeLocal()
    backend = make(server, local)
    backend.activate()
    backend.deactivate()
    backend.activate()
    assert local.get(KEY) == "h"
    local.store_quiet(KEY, None)  # the user clears it at This console
    backend.deactivate()
    backend.activate()
    assert local.get(KEY) is None  # not copied back again
    assert not server.calls


def test_key_registered_after_activation_moves(make):
    server, local = _server_with_user_value(), FakeLocal()
    backend = make(server, local)
    backend.activate()
    assert local.get(KEY) is None
    keys.register_console_keys([KEY])
    assert local.get(KEY) == "h"
    assert not server.calls


def test_inactive_backend_ignores_later_registration(make):
    server, local = _server_with_user_value(), FakeLocal()
    backend = make(server, local)
    backend.activate()
    backend.deactivate()
    keys.register_console_keys([KEY])
    assert local.get(KEY) is None


def test_non_console_key_is_not_moved(make):
    server, local = Server(), FakeLocal()
    server.rows[("user:u1", "theme")] = ("dark", 1)
    make(server, local).activate()
    assert local.get("theme") is None
