"""Logbook sync must be scoped to the current user.

Two related defects this guards against:

* Bug 2 — entries pulled from the server were stored under the *server's*
  logbook UUID rather than the local logbook keyed by the current user, so
  the panel (which lists by the local logbook id) never displayed them and
  cross-install sync silently "succeeded" with nothing visible.

* Bug 1 — the push phase pushed *every* row with ``sync_status='pending'``
  regardless of which user's logbook it belonged to, so one user's session
  could push another user's unsynced entries to the server under the wrong
  identity.
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


def test_pull_stores_entries_under_local_logbook(tmp_path, httpx_mock):
    """Pulled remote entries land in the local logbook for the current user,
    not under the server's logbook UUID."""
    from lightfall.logbook.client import _run_sync

    db_path = tmp_path / "logbook.db"
    _make_db(db_path)

    # The panel has already created a local logbook for 'alice'.
    local_logbook_id = "local-lb-alice"
    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)",
        (local_logbook_id, "alice", "2026-01-01T00:00:00+00:00"),
    )
    db.commit()
    db.close()

    # Server returns an entry that lives under a *different* (server) logbook id.
    server_logbook_id = "server-lb-uuid"
    remote_entry_id = "remote-entry-1"
    httpx_mock.add_response(
        url="http://logbook.test/logbook/entries",
        json=[
            {
                "id": remote_entry_id,
                "logbook_id": server_logbook_id,
                "title": "From another install",
                "tags": [],
                "created_at": "2026-02-01T00:00:00+00:00",
                "updated_at": "2026-02-01T00:00:00+00:00",
                "fragments": [],
            }
        ],
    )

    _run_sync(str(db_path), "http://logbook.test", user_id="alice")

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT logbook_id FROM entry WHERE id = ?", (remote_entry_id,)
    ).fetchone()
    db.close()

    assert row is not None, "pulled entry was not stored locally"
    assert row["logbook_id"] == local_logbook_id, (
        f"pulled entry stored under {row['logbook_id']!r}; expected the local "
        f"logbook {local_logbook_id!r} so the panel can display it"
    )


@pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    assert_all_requests_were_expected=False,
)
def test_push_only_sends_current_users_pending_rows(tmp_path, httpx_mock):
    """A sync run for 'alice' must not push 'bob's pending entries."""
    from lightfall.logbook.client import _run_sync

    db_path = tmp_path / "logbook.db"
    _make_db(db_path)

    db = sqlite3.connect(str(db_path))
    db.executescript(
        """
        INSERT INTO logbook (id, user_id, created_at) VALUES
            ('lb-alice', 'alice', '2026-01-01T00:00:00+00:00'),
            ('lb-bob',   'bob',   '2026-01-01T00:00:00+00:00');
        INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) VALUES
            ('entry-alice', 'lb-alice', 'A', '[]', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00', 'pending'),
            ('entry-bob',   'lb-bob',   'B', '[]', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00', 'pending');
        """
    )
    db.commit()
    db.close()

    # Accept the push (PUT succeeds) and return an empty pull.
    httpx_mock.add_response(method="PUT", status_code=200, json={})
    httpx_mock.add_response(
        method="GET", url="http://logbook.test/logbook/entries", json=[]
    )

    _run_sync(str(db_path), "http://logbook.test", user_id="alice")

    urls = [str(r.url) for r in httpx_mock.get_requests()]
    assert any("entry-alice" in u for u in urls), "alice's entry should be pushed"
    assert not any("entry-bob" in u for u in urls), (
        "bob's pending entry must NOT be pushed during alice's sync"
    )

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    statuses = {
        r["id"]: r["sync_status"]
        for r in db.execute("SELECT id, sync_status FROM entry").fetchall()
    }
    db.close()
    assert statuses["entry-alice"] == "synced"
    assert statuses["entry-bob"] == "pending", "bob's entry must stay local/pending"


@pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    assert_all_requests_were_expected=False,
)
def test_push_sends_nothing_without_user_identity(tmp_path, httpx_mock):
    """A sync with no user identity must never push any user's pending rows."""
    from lightfall.logbook.client import _run_sync

    db_path = tmp_path / "logbook.db"
    _make_db(db_path)

    db = sqlite3.connect(str(db_path))
    db.executescript(
        """
        INSERT INTO logbook (id, user_id, created_at) VALUES
            ('lb-someone', 'someone', '2026-01-01T00:00:00+00:00');
        INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) VALUES
            ('entry-x', 'lb-someone', 'X', '[]', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00', 'pending');
        """
    )
    db.commit()
    db.close()

    httpx_mock.add_response(method="PUT", status_code=200, json={})
    httpx_mock.add_response(
        method="GET", url="http://logbook.test/logbook/entries", json=[]
    )

    _run_sync(str(db_path), "http://logbook.test", user_id=None)

    methods = [r.method for r in httpx_mock.get_requests()]
    assert "PUT" not in methods and "POST" not in methods, (
        "no push requests should be made without a user identity"
    )

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    status = db.execute("SELECT sync_status FROM entry WHERE id = 'entry-x'").fetchone()[0]
    db.close()
    assert status == "pending"


def test_pull_update_migrates_stranded_logbook_id(tmp_path, httpx_mock):
    """A synced entry left under a stale logbook id migrates onto the user's
    local logbook when the server sends a newer version."""
    from lightfall.logbook.client import _run_sync

    db_path = tmp_path / "logbook.db"
    _make_db(db_path)

    local_logbook_id = "local-lb-dave"
    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)",
        (local_logbook_id, "dave", "2026-01-01T00:00:00+00:00"),
    )
    # Pre-fix leftover: a synced entry stranded under a stale logbook id.
    db.execute(
        "INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) "
        "VALUES ('entry-stranded', 'stale-lb', 'old', '[]', "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'synced')"
    )
    db.commit()
    db.close()

    httpx_mock.add_response(
        url="http://logbook.test/logbook/entries",
        json=[
            {
                "id": "entry-stranded",
                "logbook_id": "server-lb",
                "title": "updated",
                "tags": [],
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-03-01T00:00:00+00:00",
                "fragments": [],
            }
        ],
    )

    _run_sync(str(db_path), "http://logbook.test", user_id="dave")

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT logbook_id, title FROM entry WHERE id = 'entry-stranded'"
    ).fetchone()
    db.close()
    assert row["title"] == "updated"
    assert row["logbook_id"] == local_logbook_id, (
        "stranded entry should migrate onto the user's local logbook"
    )
