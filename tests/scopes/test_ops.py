"""Synchronous worker operations against a fake server."""
from __future__ import annotations

from datetime import datetime

import pytest

from lightfall.scopes.client import ScopesError, Snapshot
from lightfall.scopes.keys import client_registry
from lightfall.scopes.ops import migrate_user_prefs, push_write, replay_journal


class FakeServer:
    """Just enough of ScopesClient's write side, with versions and failures."""

    def __init__(self, offline_after=None, reject=()):
        self.rows: dict[tuple[str, str], tuple[object, int]] = {}
        self.calls: list[tuple] = []
        self.offline_after = offline_after
        self.reject = set(reject)

    def _tick(self):
        if self.offline_after is not None and len(self.calls) > self.offline_after:
            raise ScopesError("unreachable")

    def put_setting(self, scope, key, value, *, version=None, proposed_spec=None):
        self.calls.append(("put", scope, key, value, version))
        self._tick()
        if key in self.reject:
            raise ScopesError("nope", status=422)
        import json
        json.dumps(value)  # real client fails here for non-JSON values
        current = self.rows.get((scope, key))
        if current is not None and version != current[1]:
            raise ScopesError("stale", status=412)
        if current is None and version not in (None, 0):
            raise ScopesError("missing", status=412)
        new = (current[1] + 1) if current else 1
        self.rows[(scope, key)] = (value, new)
        return new

    def delete_setting(self, scope, key):
        self.calls.append(("delete", scope, key))
        self._tick()
        self.rows.pop((scope, key), None)

    def snapshot(self, scope, etag=None):
        rows = {k: v for (s, k), v in self.rows.items() if s == scope}
        return Snapshot(scope=scope, settings={k: v[0] for k, v in rows.items()},
                        versions={k: v[1] for k, v in rows.items()})


def test_push_retries_once_on_412_with_fresh_version():
    srv = FakeServer()
    srv.rows[("user:u1", "theme")] = ("light", 4)
    assert push_write(srv, "user:u1", "theme", "set", "dark", 2) == 5
    assert srv.rows[("user:u1", "theme")] == ("dark", 5)


def test_push_delete_returns_zero():
    srv = FakeServer()
    srv.rows[("user:u1", "theme")] = ("light", 1)
    assert push_write(srv, "user:u1", "theme", "delete", None, 1) == 0
    assert ("user:u1", "theme") not in srv.rows


def test_push_other_errors_propagate():
    with pytest.raises(ScopesError) as ei:
        push_write(FakeServer(reject={"theme"}), "user:u1", "theme", "set", "x", None)
    assert ei.value.status == 422


def test_migration_uploads_eligible_keys_only():
    srv = FakeServer()
    prefs = {
        "theme": "dark", "visualization.colormap": "viridis", "proxy_host": "localhost",
        "claude_api_key": "sk-secret", "telemetry_dsn": "https://k@host/3", "empty": None,
        "tiled_access_override_start": datetime(2026, 10, 1),
    }
    uploaded = migrate_user_prefs(srv, "user:u1", prefs, client_registry())
    assert sorted(uploaded) == ["theme", "visualization.colormap"]
    sent = {c[2] for c in srv.calls}
    assert "claude_api_key" not in sent and "telemetry_dsn" not in sent and "proxy_host" not in sent


def test_migration_skips_rejected_and_existing_but_stops_when_offline():
    srv = FakeServer(reject={"bad"})
    srv.rows[("user:u1", "theme")] = ("light", 1)
    assert migrate_user_prefs(srv, "user:u1", {"bad": 1, "theme": "dark", "zoom": 2},
                              client_registry()) == ["zoom"]
    offline = FakeServer(offline_after=0)
    with pytest.raises(ScopesError):
        migrate_user_prefs(offline, "user:u1", {"a": 1, "b": 2}, client_registry())


def test_replay_applies_and_reports_drops_then_stops_offline():
    srv = FakeServer(reject={"bad"})
    srv.rows[("user:u1", "theme")] = ("light", 3)
    done, dropped = replay_journal(
        srv, "user:u1",
        {"theme": {"op": "set", "value": "dark"}, "bad": {"op": "set", "value": 1},
         "gone": {"op": "delete", "value": None}},
        {"theme": 3})
    assert sorted(done) == ["gone", "theme"] and [k for k, _ in dropped] == ["bad"]
    offline = FakeServer(offline_after=0)
    assert replay_journal(offline, "user:u1", {"a": {"op": "set", "value": 1}}, {}) == ([], [])
