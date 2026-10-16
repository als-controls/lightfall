"""ScopedBackend against a fake server, with a synchronous runner."""
from __future__ import annotations

import pytest

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.client import ChainInfo, ScopesError
from lightfall.scopes.journal import WriteJournal
from lightfall.ui.preferences.scoped_backend import ScopedBackend
from tests.scopes.test_ops import FakeServer

CHAIN = ChainInfo(chain=("facility", "beamline:7.0.1", "user:u1"),
                  roles={"facility": None, "beamline:7.0.1": "operator", "user:u1": "owner"},
                  entitled=("beamline:7.0.1",), facility_label="ALS")


class Server(FakeServer):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.down = False

    def chain(self, bind):
        if self.down:
            raise ScopesError("unreachable")
        return CHAIN

    def snapshot(self, scope, etag=None):
        if self.down:
            raise ScopesError("unreachable")
        return super().snapshot(scope)

    def put_setting(self, *a, **kw):
        if self.down:
            raise ScopesError("unreachable")
        return super().put_setting(*a, **kw)

    def delete_setting(self, *a, **kw):
        if self.down:
            raise ScopesError("unreachable")
        return super().delete_setting(*a, **kw)


class FakeLocal:
    """LocalPreferenceBackend stand-in: a dict-backed ConfigManager."""

    class _CM:
        def __init__(self):
            self.data = {}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value, *, persist=False):
            if value is None:
                self.data.pop(key, None)
            else:
                self.data[key] = value

    def __init__(self, **prefs):
        self._cm = self._CM()
        for k, v in prefs.items():
            self._cm.data[f"preferences.{k}"] = v

    def get(self, key, default=None):
        return self._cm.get(f"preferences.{key}", default)


def sync_run(fn, ok, err):
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001 - mirrors QThreadFuture's except_slot
        err(e)
    else:
        ok(result)


class Deferred:
    """Runner that queues work so tests can interleave events."""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn, ok, err):
        self.jobs.append((fn, ok, err))

    def run_all(self):
        while self.jobs:
            sync_run(*self.jobs.pop(0))


@pytest.fixture
def make(tmp_path, qapp):
    def _make(server=None, local=None, run=sync_run, prefs=None):
        server = server or Server()
        local = local or FakeLocal()
        notes = []
        backend = ScopedBackend(
            local, client=server, cache=SnapshotCache(tmp_path / "cache"),
            journal=WriteJournal(tmp_path / "journal.json"), bind="beamline:7.0.1",
            identity="u1", user_preferences=lambda: dict(prefs or {}), run=run,
            notify=lambda t, m: notes.append((t, m)), poll_ms=0)
        changes = []
        backend.changed.connect(lambda k, v: changes.append((k, v)))
        return backend, server, local, changes, notes
    return _make


def test_inactive_owns_nothing(make):
    backend, *_ = make()
    assert not backend.owns("theme")


def test_activation_migrates_yaml_and_emits_server_values(make):
    backend, server, local, changes, _ = make(
        local=FakeLocal(theme="dark", proxy_host="localhost"),
        prefs={"theme": "dark", "proxy_host": "localhost", "claude_api_key": "sk"})
    server.rows[("beamline:7.0.1", "tiled_url")] = ("https://bl", 1)
    backend.activate()
    assert ("user:u1", "theme") in server.rows               # migrated
    assert ("user:u1", "proxy_host") not in server.rows      # local-only
    assert ("user:u1", "claude_api_key") not in server.rows  # secret
    assert backend.owns("theme") and not backend.owns("proxy_host")
    assert backend.get("tiled_url") == "https://bl"
    assert ("tiled_url", "https://bl") in changes


def test_server_user_values_win_and_refresh_yaml(make):
    server = Server()
    server.rows[("user:u1", "theme")] = ("light", 3)
    backend, _, local, changes, _ = make(server=server, local=FakeLocal(theme="dark"),
                                         prefs={"theme": "dark"})
    backend.activate()
    assert backend.get("theme") == "light"
    assert local.get("theme") == "light"                      # YAML is the user cache
    assert ("user:u1", "theme") in server.rows and server.rows[("user:u1", "theme")][0] == "light"
    assert ("theme", "light") in changes


def test_user_write_pushes_and_tracks_version(make):
    backend, server, local, changes, _ = make()
    backend.activate()
    backend.set("theme", "dark")
    assert server.rows[("user:u1", "theme")] == ("dark", 1)
    assert local.get("theme") == "dark"
    backend.set("theme", "light")
    assert server.rows[("user:u1", "theme")] == ("light", 2)
    assert changes[-1] == ("theme", "light")


def test_rejected_write_reverts_and_notifies(make):
    backend, server, local, changes, notes = make(server=Server(reject={"theme"}))
    backend.activate()
    backend.set("theme", "dark")
    assert backend.get("theme") is None and local.get("theme") is None
    assert changes[-2:] == [("theme", "dark"), ("theme", None)]
    assert notes and "theme" in notes[-1][1]


def test_offline_write_is_journaled_and_replayed(make, tmp_path):
    backend, server, _, _, _ = make()
    backend.activate()
    server.down = True
    backend.set("theme", "dark")
    assert backend.get("theme") == "dark"
    assert ("user:u1", "theme") not in server.rows
    server.down = False
    backend.refresh()
    assert server.rows[("user:u1", "theme")][0] == "dark"
    assert len(WriteJournal(tmp_path / "journal.json")) == 0


def test_offline_first_login_without_cache_still_works(make):
    server = Server()
    server.down = True
    backend, _, local, _, _ = make(server=server, local=FakeLocal(theme="dark"))
    backend.activate()
    assert backend.get("theme") == "dark"            # falls back to YAML
    backend.set("font_size", 12)                     # journaled, not lost
    server.down = False
    backend.refresh()
    assert server.rows[("user:u1", "font_size")][0] == 12


def test_stale_rejection_does_not_clobber_newer_value(make):
    run = Deferred()
    backend, server, _, _, _ = make(server=Server(reject={"theme"}), run=run)
    backend.activate()
    run.run_all()
    backend.set("theme", "dark")                      # will be rejected later
    backend.store.put_local("user:u1", "theme", "server-newer")  # a refresh landed first
    run.run_all()
    assert backend.get("theme") == "server-newer"


def test_callbacks_after_deactivate_are_ignored(make):
    run = Deferred()
    backend, server, local, changes, _ = make(run=run, local=FakeLocal(theme="yaml"))
    server.rows[("user:u1", "theme")] = ("srv", 1)
    backend.activate()
    run.run_all()
    assert backend.get("theme") == "srv"
    backend.set("theme", "dark")
    backend.deactivate()
    run.run_all()
    assert not backend.owns("theme")
    assert backend.store is None


def test_deactivate_emits_yaml_values_for_scope_only_keys(make):
    server = Server()
    server.rows[("beamline:7.0.1", "tiled_url")] = ("https://bl", 1)
    backend, _, _, changes, _ = make(server=server, local=FakeLocal(tiled_url="https://yaml"))
    backend.activate()
    changes.clear()
    backend.deactivate()
    assert changes == [("tiled_url", "https://yaml")]


def test_write_at_beamline_scope_sends_proposal_and_notifies_pending(make):
    class Pending(Server):
        def put_setting(self, scope, key, value, *, version=None, proposed_spec=None):
            self.calls.append(("put", scope, key, value, proposed_spec))
            raise ScopesError("not approved", status=409, code="key_pending")

    backend, server, _, _, notes = make(server=Pending())
    backend.activate()
    backend.set("tiled_url", "https://x", scope="beamline:7.0.1")
    assert server.calls[-1][4]["key"] == "tiled_url"
    assert "awaiting approval" in notes[-1][1]


def test_remove_user_value_deletes_on_server_and_yaml(make):
    server = Server()
    server.rows[("user:u1", "theme")] = ("light", 1)
    backend, _, local, changes, _ = make(server=server, local=FakeLocal(theme="light"))
    backend.activate()
    backend.remove("theme")
    assert ("user:u1", "theme") not in server.rows
    assert local.get("theme") is None
    assert changes[-1] == ("theme", None)


def test_local_only_write_is_refused_loudly(make):
    backend, *_ = make()
    backend.activate()
    with pytest.raises(ValueError):
        backend.set("claude_api_key", "sk")


def test_refresh_landing_after_logout_is_dropped(make):
    # A fetch started before logout must not rebuild the store afterwards.
    run = Deferred()
    server = Server()
    server.rows[("user:u1", "theme")] = ("srv", 1)
    backend, _, _, changes, _ = make(server=server, run=run)
    backend.activate()          # refresh queued, not yet run
    backend.deactivate()
    run.run_all()
    assert backend.store is None
    assert changes == []
