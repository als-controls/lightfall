"""load_view: server-or-cache read model with per-key resolution."""
from __future__ import annotations

import pytest

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.client import ChainInfo, ScopesError, Snapshot
from lightfall.scopes.view import load_view

CHAIN = ChainInfo(
    chain=("facility", "beamline:7.0.1", "branch:7.0.1.1", "user:u1"),
    roles={"facility": "admin", "beamline:7.0.1": "admin", "branch:7.0.1.1": "admin",
           "user:u1": "owner"},
    entitled=("beamline:7.0.1", "branch:7.0.1.1"),
    facility_label="ALS",
)
WAKE = {"key": "voice.wake_word", "status": "approved",
        "spec": {"type": "str", "scopes": ["branch", "user"], "operator_writable": True}}
PENDING = {"key": "tiled.url", "status": "pending", "spec": None,
           "proposed_spec": {"key": "tiled.url", "type": "str", "scopes": ["beamline"]}}


class FakeClient:
    def __init__(self, snaps=None, keys=(WAKE,), offline=False, chain_error=None):
        self.snaps = snaps or {}
        self.keys = keys
        self.offline = offline
        self.chain_error = chain_error
        self.etags = {}

    def _maybe_offline(self):
        if self.offline:
            raise ScopesError("unreachable")

    def chain(self, bind):
        self._maybe_offline()
        if self.chain_error is not None:
            raise self.chain_error
        return CHAIN

    def snapshot(self, scope, etag=None):
        self._maybe_offline()
        self.etags[scope] = etag
        snap = self.snaps.get(scope, Snapshot.empty(scope))
        return None if etag and etag == snap.etag else snap

    def setting_keys(self):
        self._maybe_offline()
        return None if self.keys is None else list(self.keys)


def _row(view, key):
    return next(r for r in view.keys if r.key == key)


def test_online_view_resolves_branch_value(tmp_path):
    snaps = {"branch:7.0.1.1": Snapshot(scope="branch:7.0.1.1",
                                        settings={"voice.wake_word": "hey lightfall"}, etag="b1")}
    view = load_view(FakeClient(snaps), SnapshotCache(tmp_path), "branch:7.0.1.1")
    assert view.chain == CHAIN.chain and view.offline is False
    row = _row(view, "voice.wake_word")
    assert (row.status, row.value, row.origin) == ("approved", "hey lightfall", "branch:7.0.1.1")
    assert row.scopes == ("branch", "user")
    assert _row(view, "plugins.enabled").status == "core"


def test_user_value_overrides_branch(tmp_path):
    snaps = {
        "branch:7.0.1.1": Snapshot(scope="branch:7.0.1.1", settings={"voice.wake_word": "a"}),
        "user:u1": Snapshot(scope="user:u1", settings={"voice.wake_word": "b"}),
    }
    row = _row(load_view(FakeClient(snaps), SnapshotCache(tmp_path), "branch:7.0.1.1"),
               "voice.wake_word")
    assert (row.value, row.origin) == ("b", "user:u1")


def test_lock_wins_and_is_reported(tmp_path):
    snaps = {
        "branch:7.0.1.1": Snapshot(scope="branch:7.0.1.1", settings={"voice.wake_word": "a"},
                                   locks={"voice.wake_word": {"locked_by": "rp", "reason": "demo",
                                                              "locked_at": "t"}}),
        "user:u1": Snapshot(scope="user:u1", settings={"voice.wake_word": "b"}),
    }
    row = _row(load_view(FakeClient(snaps), SnapshotCache(tmp_path), "branch:7.0.1.1"),
               "voice.wake_word")
    assert (row.value, row.locked_at, row.lock_reason) == ("a", "branch:7.0.1.1", "demo")


def test_invalid_value_falls_back_and_is_flagged(tmp_path):
    snaps = {
        "branch:7.0.1.1": Snapshot(scope="branch:7.0.1.1", settings={"voice.wake_word": "a"}),
        "user:u1": Snapshot(scope="user:u1", settings={"voice.wake_word": 42}),
    }
    row = _row(load_view(FakeClient(snaps), SnapshotCache(tmp_path), "branch:7.0.1.1"),
               "voice.wake_word")
    assert (row.value, row.origin) == ("a", "branch:7.0.1.1")
    assert row.rejected_at == ("user:u1",)


def test_pending_key_listed_without_value(tmp_path):
    view = load_view(FakeClient(keys=(WAKE, PENDING)), SnapshotCache(tmp_path), "branch:7.0.1.1")
    row = _row(view, "tiled.url")
    assert (row.status, row.value, row.origin, row.scopes) == ("pending", None, None, ("beamline",))


def test_non_maintainer_sees_core_keys_and_restricted_flag(tmp_path):
    view = load_view(FakeClient(keys=None), SnapshotCache(tmp_path), "branch:7.0.1.1")
    assert view.keys_restricted is True
    assert [r.key for r in view.keys] == ["plugins.enabled"]


def test_undeclared_snapshot_key_shown_as_user_only(tmp_path):
    snaps = {"user:u1": Snapshot(scope="user:u1", settings={"theme": "dark"})}
    row = _row(load_view(FakeClient(snaps), SnapshotCache(tmp_path), "branch:7.0.1.1"), "theme")
    assert (row.status, row.value, row.origin, row.scopes) == ("undeclared", "dark", "user:u1",
                                                              ("user",))


def test_second_load_uses_etag_and_cache(tmp_path):
    cache = SnapshotCache(tmp_path)
    snaps = {"branch:7.0.1.1": Snapshot(scope="branch:7.0.1.1",
                                        settings={"voice.wake_word": "x"}, etag="b1")}
    client = FakeClient(snaps)
    load_view(client, cache, "branch:7.0.1.1")
    view = load_view(client, cache, "branch:7.0.1.1")
    assert client.etags["branch:7.0.1.1"] == "b1"  # conditional GET -> 304 -> cache
    assert _row(view, "voice.wake_word").value == "x"


def test_offline_after_good_load_uses_cache(tmp_path):
    cache = SnapshotCache(tmp_path)
    snaps = {"branch:7.0.1.1": Snapshot(scope="branch:7.0.1.1", settings={"voice.wake_word": "x"})}
    load_view(FakeClient(snaps), cache, "branch:7.0.1.1")
    view = load_view(FakeClient(offline=True), cache, "branch:7.0.1.1")
    assert view.offline is True and view.cached_at is not None
    assert view.chain == CHAIN.chain
    assert _row(view, "voice.wake_word").value == "x"


def test_offline_with_no_cache_raises(tmp_path):
    with pytest.raises(ScopesError):
        load_view(FakeClient(offline=True), SnapshotCache(tmp_path), "branch:7.0.1.1")


def test_not_logged_in_raises_401_even_with_cache(tmp_path):
    cache = SnapshotCache(tmp_path)
    load_view(FakeClient(), cache, "branch:7.0.1.1")
    with pytest.raises(ScopesError) as ei:
        load_view(FakeClient(chain_error=ScopesError("no", status=401)), cache, "branch:7.0.1.1")
    assert ei.value.status == 401
