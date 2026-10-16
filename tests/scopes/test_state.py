"""fetch_state: chain + one snapshot per scope, server or cache."""
from __future__ import annotations

import pytest

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.client import ScopesError, Snapshot
from lightfall.scopes.state import fetch_state
from tests.scopes.test_view import CHAIN, FakeClient


def test_online_state_has_every_chain_scope(tmp_path):
    snaps = {"user:u1": Snapshot(scope="user:u1", settings={"theme": "dark"}, versions={"theme": 2})}
    state = fetch_state(FakeClient(snaps), SnapshotCache(tmp_path), "branch:7.0.1.1", "u1")
    assert list(state.snapshots) == list(CHAIN.chain)
    assert state.snapshots["user:u1"].versions == {"theme": 2}
    assert state.offline is False and state.cached_at is None


def test_offline_state_from_cache(tmp_path):
    cache = SnapshotCache(tmp_path)
    fetch_state(FakeClient(), cache, "branch:7.0.1.1", "u1")
    state = fetch_state(FakeClient(offline=True), cache, "branch:7.0.1.1", "u1")
    assert state.offline is True and state.cached_at is not None


def test_offline_logged_out_raises_401(tmp_path):
    with pytest.raises(ScopesError) as ei:
        fetch_state(FakeClient(offline=True), SnapshotCache(tmp_path), "facility", None)
    assert ei.value.status == 401
