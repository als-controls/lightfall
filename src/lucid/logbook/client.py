"""
Local-first logbook client with optional server sync.

Provides a singleton ``LogbookClient`` that persists logbook data in a local
SQLite database (``~/.lucid/logbook.db``). All writes go to the local
database first (offline-first). Sync to a remote server happens in a
background thread when configured.

Uses synchronous sqlite3 for simplicity — local disk I/O is fast enough
and avoids async/Qt event loop integration issues.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal, QObject

from lucid.utils.logging import logger

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS logbook (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry (
    id          TEXT PRIMARY KEY,
    logbook_id  TEXT NOT NULL REFERENCES logbook(id),
    title       TEXT,
    tags        TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    sync_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS fragment (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL REFERENCES entry(id),
    position    INTEGER NOT NULL DEFAULT 0,
    kind        TEXT NOT NULL DEFAULT 'text',
    subtype     TEXT,
    content     TEXT NOT NULL DEFAULT '',
    data        TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    sync_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_entry_logbook ON entry(logbook_id);
CREATE INDEX IF NOT EXISTS idx_fragment_entry ON fragment(entry_id);
"""


class _SyncWorker(QThread):
    """Background thread for server sync."""

    finished = Signal(int, int)  # (pushed, pulled)

    def __init__(self, db_path: str, server_url: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db_path = db_path
        self._server_url = server_url

    def run(self) -> None:
        if httpx is None:
            return
        pushed = pulled = 0
        try:
            db = sqlite3.connect(self._db_path)
            db.row_factory = sqlite3.Row
            with httpx.Client(base_url=self._server_url, timeout=10) as client:
                # Push pending entries
                for row in db.execute("SELECT * FROM entry WHERE sync_status = 'pending'"):
                    r = dict(row)
                    r["tags"] = json.loads(r["tags"]) if isinstance(r["tags"], str) else r["tags"]
                    try:
                        resp = client.put(f"/api/entries/{r['id']}", json=r)
                        if resp.is_success:
                            db.execute("UPDATE entry SET sync_status = 'synced' WHERE id = ?", (r["id"],))
                            pushed += 1
                    except Exception:
                        pass

                # Push pending fragments
                for row in db.execute("SELECT * FROM fragment WHERE sync_status = 'pending'"):
                    r = dict(row)
                    if r.get("data") and isinstance(r["data"], str):
                        r["data"] = json.loads(r["data"])
                    try:
                        resp = client.put(f"/api/fragments/{r['id']}", json=r)
                        if resp.is_success:
                            db.execute("UPDATE fragment SET sync_status = 'synced' WHERE id = ?", (r["id"],))
                            pushed += 1
                    except Exception:
                        pass

                db.commit()
            db.close()
        except Exception as exc:
            logger.warning("Sync failed: {}", exc)

        self.finished.emit(pushed, pulled)


class LogbookClient:
    """Offline-first logbook persistence with optional remote sync.

    All operations are synchronous (local SQLite). Remote sync runs
    in a background QThread.

    Usage::

        client = LogbookClient.get_instance()
        client.init()
        logbook_id = client.get_or_create_logbook("rp")
        entry_id = client.create_entry(logbook_id, title="Shift 1")
    """

    _instance: LogbookClient | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._db: sqlite3.Connection | None = None
        self._db_path = Path.home() / ".lucid" / "logbook.db"
        self._server_url: str | None = None
        self._offline_only = False
        self._initialized = False
        self._sync_worker: _SyncWorker | None = None

    @classmethod
    def get_instance(cls) -> LogbookClient:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def init(self) -> None:
        """Open/create the local database and apply schema."""
        if self._initialized:
            return

        self._load_preferences()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = sqlite3.connect(str(self._db_path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self._initialized = True
        logger.info("LogbookClient initialised (db={})", self._db_path)

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
        self._initialized = False

    def _load_preferences(self) -> None:
        try:
            from lucid.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
            self._server_url = prefs.get("logbook_url", None)
            self._offline_only = prefs.get("logbook_offline_only", False)
        except Exception:
            logger.debug("Could not load logbook preferences, using defaults")

    def _ensure_db(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("LogbookClient not initialised — call init() first")
        return self._db

    # ── Logbook ───────────────────────────────────────────────────

    def get_or_create_logbook(self, user_id: str) -> str:
        db = self._ensure_db()
        row = db.execute("SELECT id FROM logbook WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return row["id"]
        logbook_id = _uuid()
        db.execute(
            "INSERT INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)",
            (logbook_id, user_id, _now()),
        )
        db.commit()
        logger.info("Created logbook {} for user {}", logbook_id, user_id)
        return logbook_id

    # ── Entry CRUD ────────────────────────────────────────────────

    def create_entry(
        self,
        logbook_id: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        entry_id: str | None = None,
    ) -> str:
        db = self._ensure_db()
        eid = entry_id or _uuid()
        now = _now()
        db.execute(
            "INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (eid, logbook_id, title, json.dumps(tags or []), now, now),
        )
        db.commit()
        return eid

    def list_entries(self, logbook_id: str) -> list[dict[str, Any]]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM entry WHERE logbook_id = ? ORDER BY created_at DESC",
            (logbook_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        db = self._ensure_db()
        row = db.execute("SELECT * FROM entry WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None

    def update_entry(
        self, entry_id: str, *, title: str | None = None, tags: list[str] | None = None
    ) -> None:
        db = self._ensure_db()
        parts: list[str] = ["updated_at = ?", "sync_status = 'pending'"]
        params: list[Any] = [_now()]
        if title is not None:
            parts.append("title = ?")
            params.append(title)
        if tags is not None:
            parts.append("tags = ?")
            params.append(json.dumps(tags))
        params.append(entry_id)
        db.execute(f"UPDATE entry SET {', '.join(parts)} WHERE id = ?", params)
        db.commit()

    # ── Fragment CRUD ─────────────────────────────────────────────

    def add_fragment(
        self,
        entry_id: str,
        *,
        kind: str = "text",
        subtype: str | None = None,
        content: str = "",
        data: dict[str, Any] | None = None,
        position: int | None = None,
        fragment_id: str | None = None,
    ) -> str:
        db = self._ensure_db()
        fid = fragment_id or _uuid()
        now = _now()
        if position is None:
            row = db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM fragment WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            position = row["pos"] if row else 0
        db.execute(
            "INSERT INTO fragment (id, entry_id, position, kind, subtype, content, data, "
            "created_at, updated_at, sync_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (fid, entry_id, position, kind, subtype, content,
             json.dumps(data) if data else None, now, now),
        )
        db.commit()
        return fid

    def update_fragment(
        self,
        fragment_id: str,
        *,
        content: str | None = None,
        data: dict[str, Any] | None = None,
        position: int | None = None,
    ) -> None:
        db = self._ensure_db()
        parts: list[str] = ["updated_at = ?", "sync_status = 'pending'"]
        params: list[Any] = [_now()]
        if content is not None:
            parts.append("content = ?")
            params.append(content)
        if data is not None:
            parts.append("data = ?")
            params.append(json.dumps(data))
        if position is not None:
            parts.append("position = ?")
            params.append(position)
        params.append(fragment_id)
        db.execute(f"UPDATE fragment SET {', '.join(parts)} WHERE id = ?", params)
        db.commit()

    def delete_fragment(self, fragment_id: str) -> None:
        db = self._ensure_db()
        db.execute("DELETE FROM fragment WHERE id = ?", (fragment_id,))
        db.commit()

    def list_fragments(self, entry_id: str) -> list[dict[str, Any]]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM fragment WHERE entry_id = ? ORDER BY position",
            (entry_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Sync ──────────────────────────────────────────────────────

    def schedule_sync(self) -> None:
        """Start a background sync if configured and not already running."""
        if self._offline_only or not self._server_url:
            return
        if self._sync_worker and self._sync_worker.isRunning():
            return
        self._sync_worker = _SyncWorker(str(self._db_path), self._server_url)
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.start()

    def _on_sync_done(self, pushed: int, pulled: int) -> None:
        if pushed or pulled:
            logger.info("Sync complete: {} pushed, {} pulled", pushed, pulled)
