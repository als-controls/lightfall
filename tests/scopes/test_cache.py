"""Disk cache for scope snapshots and chains."""
from __future__ import annotations

import json

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.client import ChainInfo, Snapshot


def test_round_trip_snapshot(tmp_path):
    cache = SnapshotCache(tmp_path / "c")
    snap = Snapshot(scope="branch:7.0.1.1", settings={"a": 1}, versions={"a": 1}, etag="e")
    cache.save_snapshot(snap)
    loaded, saved_at = cache.load_snapshot("branch:7.0.1.1")
    assert loaded == snap
    assert saved_at > 0


def test_file_names_are_windows_safe(tmp_path):
    cache = SnapshotCache(tmp_path)
    assert cache.path_for("branch:7.0.1.1").name == "branch-7.0.1.1.json"
    assert cache.path_for("user:a/b\\c").name == "user-a_b_c.json"


def test_missing_returns_none(tmp_path):
    assert SnapshotCache(tmp_path).load_snapshot("facility") is None


def test_corrupt_file_returns_none(tmp_path):
    cache = SnapshotCache(tmp_path)
    cache.path_for("facility").write_text("{not json", encoding="utf-8")
    assert cache.load_snapshot("facility") is None


def test_wrong_scope_in_file_returns_none(tmp_path):
    cache = SnapshotCache(tmp_path)
    cache.path_for("facility").write_text(
        json.dumps({"saved_at": 1.0, "data": {"scope": "beamline:x"}}), encoding="utf-8")
    assert cache.load_snapshot("facility") is None


def test_chain_round_trip(tmp_path):
    cache = SnapshotCache(tmp_path)
    info = ChainInfo(chain=("facility", "user:u"), roles={"facility": None, "user:u": "owner"},
                     entitled=(), facility_label="ALS")
    cache.save_chain("facility", info, "alice")
    assert cache.load_chain("facility", "alice")[0] == info
    assert cache.load_chain("facility", "bob") is None


def test_keys_round_trip(tmp_path):
    cache = SnapshotCache(tmp_path)
    assert cache.load_keys("alice") is None
    cache.save_keys([{"key": "voice.wake_word", "status": "approved"}], "alice")
    assert cache.load_keys("alice") == [{"key": "voice.wake_word", "status": "approved"}]
    assert cache.load_keys("bob") is None


def test_save_leaves_no_temp_files(tmp_path):
    cache = SnapshotCache(tmp_path)
    cache.save_snapshot(Snapshot(scope="facility"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["facility.json"]
