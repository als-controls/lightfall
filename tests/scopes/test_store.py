"""ScopedStore: in-memory chain snapshots with resolution and optimistic edits."""
from __future__ import annotations

from lightfall.scopes.client import Snapshot
from lightfall.scopes.keys import client_registry
from lightfall.scopes.state import FetchedState
from lightfall.scopes.store import MISSING, ScopedStore
from tests.scopes.test_view import CHAIN


def _state(**by_scope):
    snaps = {s: by_scope.get(s.replace(":", "_").replace(".", "_"), Snapshot.empty(s))
             for s in CHAIN.chain}
    return FetchedState(chain=CHAIN, snapshots=snaps, offline=False, cached_at=None)


def _store(**by_scope):
    return ScopedStore(client_registry(), _state(**by_scope))


def test_user_scope_and_values():
    s = _store(user_u1=Snapshot(scope="user:u1", settings={"theme": "dark"}, versions={"theme": 3}))
    assert s.user_scope == "user:u1"
    assert s.value("theme") == "dark"
    assert s.version("user:u1", "theme") == 3
    assert s.value("font_size") is MISSING


def test_undeclared_key_ignores_higher_scopes():
    s = _store(branch_7_0_1_1=Snapshot(scope="branch:7.0.1.1", settings={"theme": "light"}))
    assert s.value("theme") is MISSING  # theme is user-only


def test_declared_key_inherits_and_user_overrides():
    s = _store(beamline_7_0_1=Snapshot(scope="beamline:7.0.1", settings={"tiled_url": "https://a"}))
    assert s.value("tiled_url") == "https://a"
    s.put_local("user:u1", "tiled_url", "https://mine")
    assert s.value("tiled_url") == "https://mine"
    s.drop_local("user:u1", "tiled_url")
    assert s.value("tiled_url") == "https://a"


def test_lock_above_user_wins():
    s = _store(beamline_7_0_1=Snapshot(
        scope="beamline:7.0.1", settings={"tiled_url": "https://a"},
        locks={"tiled_url": {"locked_by": "rp", "reason": "", "locked_at": ""}}))
    s.put_local("user:u1", "tiled_url", "https://mine")
    assert s.value("tiled_url") == "https://a"


def test_restore_and_versions():
    s = _store()
    s.put_local("user:u1", "theme", "dark")
    s.set_version("user:u1", "theme", 5)
    s.restore("user:u1", "theme", MISSING, None)
    assert s.user_value("theme") is MISSING and s.version("user:u1", "theme") is None
    s.restore("user:u1", "theme", "light", 2)
    assert (s.user_value("theme"), s.version("user:u1", "theme")) == ("light", 2)


def test_keys_and_replace():
    s = _store(user_u1=Snapshot(scope="user:u1", settings={"theme": "dark"}))
    assert s.keys() == {"theme"}
    s.replace(_state(user_u1=Snapshot(scope="user:u1", settings={"font_size": 12})))
    assert s.keys() == {"font_size"} and s.user_settings() == {"font_size": 12}
