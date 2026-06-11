"""Deleting a logbook entry must propagate to the server and stay deleted.

Before this fix, ``delete_entry`` hard-deleted the row locally but the deletion
was never pushed to the server, so the next pull (e.g. on restart) re-inserted
the entry. Deletions are now tombstoned, pushed as ``DELETE``, then purged; and
pull reconciles entries deleted on other installs.
"""
from __future__ import annotations

import sqlite3

import pytest

_SCHEMA = """
CREATE TABLE logbook (id TEXT PRIMARY KEY, user_id TEXT UNIQUE, created_at TEXT);
CREATE TABLE entry (id TEXT PRIMARY KEY, logbook_id TEXT, title TEXT,
                    tags TEXT DEFAULT '[]', created_at TEXT, updated_at TEXT,
                    sync_status TEXT DEFAULT 'pending');
CREATE TABLE fragment (id TEXT PRIMARY KEY, entry_id TEXT, position INTEGER,
                       kind TEXT, subtype TEXT, content TEXT, data TEXT,
                       created_at TEXT, updated_at TEXT,
                       sync_status TEXT DEFAULT 'pending');
CREATE TABLE image_sync (image_id TEXT PRIMARY KEY, local_path TEXT,
                         sync_status TEXT DEFAULT 'pending_upload');
"""


def _make_db(path) -> None:
    db = sqlite3.connect(str(path))
    db.executescript(_SCHEMA)
    db.commit()
    db.close()


def _seed_logbook(path, user_id="alice", logbook_id="lb-alice"):
    db = sqlite3.connect(str(path))
    db.execute(
        "INSERT INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)",
        (logbook_id, user_id, "2026-01-01T00:00:00+00:00"),
    )
    db.commit()
    db.close()
    return logbook_id


# ── _run_sync: delete propagation ─────────────────────────────────────────


@pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    assert_all_requests_were_expected=False,
)
def test_tombstoned_entry_is_deleted_on_server_then_purged(tmp_path, httpx_mock):
    from lightfall.logbook.client import _run_sync

    db_path = tmp_path / "logbook.db"
    _make_db(db_path)
    lb = _seed_logbook(db_path)

    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) "
        "VALUES ('entry-del', ?, 'doomed', '[]', '2026-01-02T00:00:00+00:00', "
        "'2026-01-02T00:00:00+00:00', 'deleted')",
        (lb,),
    )
    db.commit()
    db.close()

    httpx_mock.add_response(method="DELETE", status_code=204)
    # Server has already dropped it -> pull returns empty.
    httpx_mock.add_response(method="GET", url="http://lb.test/logbook/entries", json=[])

    _run_sync(str(db_path), "http://lb.test", user_id="alice")

    urls = [str(r.url) for r in httpx_mock.get_requests() if r.method == "DELETE"]
    assert any("entry-del" in u for u in urls), "a DELETE must be sent for the tombstone"

    db = sqlite3.connect(str(db_path))
    remaining = db.execute("SELECT id FROM entry WHERE id = 'entry-del'").fetchone()
    db.close()
    assert remaining is None, "entry must be purged locally after the server delete"


@pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    assert_all_requests_were_expected=False,
)
def test_pull_does_not_resurrect_a_tombstoned_entry(tmp_path, httpx_mock):
    """If the server still returns the entry while a tombstone is pending, the
    tombstone must not be flipped back to a live/synced row."""
    from lightfall.logbook.client import _run_sync

    db_path = tmp_path / "logbook.db"
    _make_db(db_path)
    lb = _seed_logbook(db_path)

    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) "
        "VALUES ('entry-del', ?, 'doomed', '[]', '2026-01-02T00:00:00+00:00', "
        "'2026-01-02T00:00:00+00:00', 'deleted')",
        (lb,),
    )
    db.commit()
    db.close()

    # DELETE fails (server briefly unreachable for that verb) so the tombstone
    # survives; the pull in the same run still sees the entry on the server.
    httpx_mock.add_response(method="DELETE", status_code=500)
    httpx_mock.add_response(
        method="GET",
        url="http://lb.test/logbook/entries",
        json=[
            {
                "id": "entry-del",
                "logbook_id": "server-lb",
                "title": "doomed",
                "tags": [],
                "created_at": "2026-01-02T00:00:00+00:00",
                "updated_at": "2026-09-09T00:00:00+00:00",
                "fragments": [],
            }
        ],
    )

    _run_sync(str(db_path), "http://lb.test", user_id="alice")

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT sync_status FROM entry WHERE id = 'entry-del'").fetchone()
    db.close()
    assert row is not None and row["sync_status"] == "deleted", (
        "a failed delete must keep the tombstone, not resurrect the entry"
    )


def test_pull_removes_entry_deleted_on_another_install(tmp_path, httpx_mock):
    """A synced local entry that the server no longer lists was deleted
    elsewhere and must be removed locally."""
    from lightfall.logbook.client import _run_sync

    db_path = tmp_path / "logbook.db"
    _make_db(db_path)
    lb = _seed_logbook(db_path)

    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) "
        "VALUES ('entry-gone', ?, 'stale', '[]', '2026-01-02T00:00:00+00:00', "
        "'2026-01-02T00:00:00+00:00', 'synced')",
        (lb,),
    )
    db.commit()
    db.close()

    httpx_mock.add_response(url="http://lb.test/logbook/entries", json=[])

    _run_sync(str(db_path), "http://lb.test", user_id="alice")

    db = sqlite3.connect(str(db_path))
    gone = db.execute("SELECT id FROM entry WHERE id = 'entry-gone'").fetchone()
    db.close()
    assert gone is None, "synced entry absent from server should be removed locally"


@pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    assert_all_requests_were_expected=False,
)
def test_pull_keeps_pending_local_entry_absent_from_server(tmp_path, httpx_mock):
    """A brand-new local (pending) entry not yet on the server must NOT be
    removed by reconciliation."""
    from lightfall.logbook.client import _run_sync

    db_path = tmp_path / "logbook.db"
    _make_db(db_path)
    lb = _seed_logbook(db_path)

    db = sqlite3.connect(str(db_path))
    # Pending entry; mock the push so it 'succeeds' but server pull returns [].
    db.execute(
        "INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) "
        "VALUES ('entry-new', ?, 'fresh', '[]', '2026-01-02T00:00:00+00:00', "
        "'2026-01-02T00:00:00+00:00', 'pending')",
        (lb,),
    )
    db.commit()
    db.close()

    httpx_mock.add_response(method="PUT", status_code=200, json={})
    httpx_mock.add_response(method="POST", status_code=201, json={})
    httpx_mock.add_response(method="GET", url="http://lb.test/logbook/entries", json=[])

    _run_sync(str(db_path), "http://lb.test", user_id="alice")

    db = sqlite3.connect(str(db_path))
    still = db.execute("SELECT id FROM entry WHERE id = 'entry-new'").fetchone()
    db.close()
    assert still is not None, "a freshly-created local entry must survive reconciliation"


# ── LogbookClient.delete_entry: tombstone semantics ───────────────────────


@pytest.fixture
def offline_client(tmp_path, monkeypatch):
    """A LogbookClient on a temp DB with sync disabled (no Qt timer needed)."""
    from lightfall.logbook.client import LogbookClient

    LogbookClient.reset()
    client = LogbookClient.get_instance()
    monkeypatch.setattr(client, "_db_path", tmp_path / "logbook.db")
    monkeypatch.setattr(client, "_load_preferences", lambda: None)
    client._server_url = None  # schedule_sync() early-returns -> no QTimer
    client.init()
    yield client
    client.close()
    LogbookClient.reset()


def test_delete_synced_entry_tombstones_and_hides_it(offline_client):
    c = offline_client
    lb = c.get_or_create_logbook("alice")
    eid = c.create_entry(lb, title="keep-then-delete")
    # Simulate it having been synced.
    db = c._ensure_db()
    db.execute("UPDATE entry SET sync_status = 'synced' WHERE id = ?", (eid,))
    db.commit()

    c.delete_entry(eid)

    # Row still present as a tombstone (so the delete can be pushed)...
    row = db.execute("SELECT sync_status FROM entry WHERE id = ?", (eid,)).fetchone()
    assert row is not None and row["sync_status"] == "deleted"
    # ...but it must not appear in the listing.
    assert eid not in [e["id"] for e in c.list_entries(lb)]


def test_delete_pending_entry_is_local_only(offline_client):
    c = offline_client
    lb = c.get_or_create_logbook("alice")
    eid = c.create_entry(lb, title="never-synced")  # stays 'pending'

    c.delete_entry(eid)

    db = c._ensure_db()
    row = db.execute("SELECT id FROM entry WHERE id = ?", (eid,)).fetchone()
    assert row is None, "a never-synced entry should be hard-deleted, not tombstoned"
