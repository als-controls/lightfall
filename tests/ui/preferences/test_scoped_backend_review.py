"""Regressions for the phase-4b final review (C1, I1-I7)."""
# ruff: noqa: F811  (the make fixture is imported from test_scoped_backend)
from __future__ import annotations

import datetime

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.client import ChainInfo, ScopesError
from lightfall.scopes.journal import WriteJournal
from lightfall.ui.preferences.scoped_backend import ScopedBackend
from tests.ui.preferences.test_scoped_backend import (
    Deferred,
    FakeLocal,
    Server,
    make,  # noqa: F401, F811 - fixture
    sync_run,
)


class FlakyServer(Server):
    """Server whose next calls can fail with a chosen HTTP status."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.put_status: int | None = None
        self.snapshot_status: dict[str, int] = {}

    def put_setting(self, scope, key, value, *, version=None, proposed_spec=None):
        if self.put_status is not None:
            raise ScopesError("bad gateway", status=self.put_status)
        return super().put_setting(scope, key, value, version=version,
                                   proposed_spec=proposed_spec)

    def snapshot(self, scope, etag=None):
        if scope in self.snapshot_status:
            raise ScopesError("boom", status=self.snapshot_status[scope])
        return super().snapshot(scope, etag)


def _yaml_prefs(local):
    return lambda: {k.split(".", 1)[1]: v for k, v in local._cm.data.items()}


# C1 ------------------------------------------------------------------------

def test_shared_console_never_hands_one_users_values_to_the_next(tmp_path, qapp):
    class Who(Server):
        who = "bob"

        def chain(self, bind):
            return ChainInfo(chain=("facility", f"user:{self.who}"), roles={}, entitled=(),
                             facility_label="ALS")

    server = Who()
    server.rows[("user:bob", "profile_image_id")] = ("bob-avatar", 1)
    server.rows[("user:bob", "theme")] = ("bob-theme", 1)
    local = FakeLocal()

    def mk(who):
        return ScopedBackend(local, client=server, cache=SnapshotCache(tmp_path / "c"),
                             journal=WriteJournal(tmp_path / f"j-{who}.json"), bind="facility",
                             identity=who, user_preferences=_yaml_prefs(local), run=sync_run,
                             notify=lambda *a: None, poll_ms=0)

    bob = mk("bob")
    bob.activate()
    assert bob.get("theme") == "bob-theme"
    bob.deactivate()
    assert local.get("theme") is None          # nothing of bob's lands on the console

    server.who = "alice"
    alice = mk("alice")
    alice.activate()
    assert ("user:alice", "profile_image_id") not in server.rows
    assert ("user:alice", "theme") not in server.rows
    assert alice.get("theme") is None


def test_foreign_yaml_is_not_read_offline(tmp_path, qapp):
    local = FakeLocal(theme="bob-theme")
    local._cm.data["preferences._scoped_cache_owner"] = "bob"
    server = Server()
    server.down = True
    alice = ScopedBackend(local, client=server, cache=SnapshotCache(tmp_path / "c"),
                          journal=WriteJournal(tmp_path / "j.json"), bind="facility",
                          identity="alice", run=sync_run, notify=lambda *a: None, poll_ms=0)
    alice.activate()
    assert alice.get("theme") is None


# I1 / I2 -------------------------------------------------------------------

def test_refresh_overlapping_an_inflight_write_keeps_it(make):
    run = Deferred()
    backend, server, local, _, _ = make(run=run)
    backend.activate()
    run.run_all()
    backend.set("theme", "dark")
    backend.refresh()
    push, fetch = run.jobs.pop(0), run.jobs.pop(0)
    state = fetch[0]()          # snapshot taken before the PUT commits
    sync_run(*push)
    fetch[1](state)             # stale refresh lands afterwards
    assert backend.get("theme") == "dark"
    assert local.get("theme") is None


def test_write_inflight_at_logout_is_replayed_next_login(make, tmp_path):
    run = Deferred()
    backend, server, _, _, _ = make(run=run)
    backend.activate()
    run.run_all()
    server.down = True
    backend.set("theme", "dark")
    backend.deactivate()
    run.run_all()
    assert len(WriteJournal(tmp_path / "journal.json")) == 1
    server.down = False
    backend.activate()
    run.run_all()
    assert server.rows[("user:u1", "theme")][0] == "dark"


# I3 ------------------------------------------------------------------------

def test_non_json_value_stays_local_and_does_not_poison_later_writes(make):
    backend, server, local, _, _ = make()
    backend.activate()
    when = datetime.datetime(2026, 1, 1)
    backend.set("tiled_access_override_start", when)
    assert local.get("tiled_access_override_start") == when
    assert ("user:u1", "tiled_access_override_start") not in server.rows
    server.down = True
    backend.set("font_size", 12)
    server.down = False
    backend.refresh()
    assert server.rows[("user:u1", "font_size")][0] == 12


# I4 ------------------------------------------------------------------------

def test_server_error_keeps_the_write_and_journals_it(make, tmp_path):
    backend, server, _, _, notes = make(server=FlakyServer())
    backend.activate()
    server.put_status = 503
    backend.set("theme", "dark")
    assert backend.get("theme") == "dark"
    assert notes == []
    assert "theme" in WriteJournal(tmp_path / "journal.json").entries()
    server.put_status = None
    backend.refresh()
    assert server.rows[("user:u1", "theme")][0] == "dark"


# I5 ------------------------------------------------------------------------

def test_failed_user_snapshot_does_not_wipe_yaml_or_migrate(make):
    server = FlakyServer()
    server.snapshot_status["user:u1"] = 500
    local = FakeLocal(theme="dark")
    backend, _, _, _, _ = make(server=server, local=local, prefs={"theme": "dark"})
    backend.activate()
    assert local.get("theme") == "dark"
    assert ("user:u1", "theme") not in server.rows


# I6 ------------------------------------------------------------------------

def test_migration_runs_even_when_legacy_rows_exist(make):
    server = Server()
    server.rows[("user:u1", "profile_image_id")] = ("img", 1)   # moved by the server
    backend, _, _, _, _ = make(server=server, local=FakeLocal(theme="dark"),
                               prefs={"theme": "dark", "profile_image_id": "old"},
                               bind="facility")
    backend.activate()
    assert server.rows[("user:u1", "theme")][0] == "dark"
    assert server.rows[("user:u1", "profile_image_id")][0] == "img"  # server value kept


def test_partial_migration_resumes_on_next_refresh(make):
    server = Server(offline_after=0)           # first PUT fails in transport
    backend, _, _, _, _ = make(server=server, local=FakeLocal(a=1, b=2),
                               prefs={"a": 1, "b": 2}, bind="facility")
    backend.activate()
    server.offline_after = None
    backend.refresh()
    assert server.rows[("user:u1", "a")][0] == 1
    assert server.rows[("user:u1", "b")][0] == 2


# I7 ------------------------------------------------------------------------

def test_yaml_cache_uses_beamline_specific_paths(qapp):
    from lightfall.ui.preferences.backend import LocalPreferenceBackend

    class CM:
        def __init__(self):
            self.data = {}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value, *, persist=False):
            self.data[key] = value

    cm = CM()
    local = LocalPreferenceBackend(cm, beamline="7.0.1")
    emitted = []
    local.changed.connect(lambda k, v: emitted.append(k))
    local.store_quiet("default_data_dir", "/data")
    assert cm.data == {"preferences.beamlines.7.0.1.default_data_dir": "/data"}
    assert local.get("default_data_dir") == "/data"
    assert emitted == []


def test_unchanged_value_is_not_sent_again(make):
    # Preferences "Apply" saves every page; unchanged keys must not bump versions.
    server = Server()
    server.rows[("user:u1", "theme")] = ("light", 1)
    backend, _, _, _, _ = make(server=server, local=FakeLocal(theme="light"),
                               prefs={"theme": "light"})
    backend.activate()
    puts = [c for c in server.calls if c[0] == "put" and c[2] == "theme"]
    backend.set("theme", "light")
    assert [c for c in server.calls if c[0] == "put" and c[2] == "theme"] == puts
    assert server.rows[("user:u1", "theme")] == ("light", 1)
    backend.set("theme", "dark")
    assert server.rows[("user:u1", "theme")] == ("dark", 2)
