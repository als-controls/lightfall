"""Verify LogbookClient sends Apikey auth on sync requests."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from lucid.auth.service_key import MintedKey
from lucid.auth.session import SessionManager


@pytest.fixture(autouse=True)
def reset_singleton():
    SessionManager.reset()
    yield
    SessionManager.reset()


def test_run_sync_carries_apikey_header(tmp_path, httpx_mock):
    """The sync worker injects Authorization: Apikey <secret> on requests."""
    from lucid.logbook.client import _run_sync

    # Seed the session-key cache directly — the production write path is
    # the login mint, which is tested separately. Here we just need the
    # cache populated so ServiceKeyAuth has a key to inject.
    sm = SessionManager.get_instance()
    sm._service_keys["logbook"] = MintedKey(
        secret="apikey-secret",
        first_eight="apikey-s",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=(),
        note="test",
    )

    # Empty local DB — no pending rows to push.
    db_path = tmp_path / "logbook.db"
    db = sqlite3.connect(str(db_path))
    db.executescript(
        """
        CREATE TABLE logbook (id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT);
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
    )
    db.commit()
    db.close()

    # Mock the GET /logbook/entries pull (the only request made when DB is empty).
    httpx_mock.add_response(
        url="http://logbook.test/logbook/entries",
        json=[],
    )

    _run_sync(str(db_path), "http://logbook.test", user_id="tester")

    # Verify the request carried the Apikey header
    requests = httpx_mock.get_requests()
    assert len(requests) >= 1, "expected at least one HTTP request"
    auth_header = requests[0].headers.get("Authorization", "")
    assert auth_header == "Apikey apikey-secret", (
        f"expected 'Apikey apikey-secret', got {auth_header!r}"
    )
