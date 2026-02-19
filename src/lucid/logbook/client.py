"""
Local-first logbook client with optional server sync.

Provides a singleton ``LogbookClient`` that persists logbook data in a local
SQLite database (``~/.lucid/logbook.db``) and optionally syncs to a remote
logbook server via HTTP.

All writes go to the local database first (offline-first). The sync methods
push/pull changes when the server is reachable.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer

from lucid.utils.logging import logger

try:
    import aiosqlite
except ImportError:  # pragma: no cover
    aiosqlite = None  # type: ignore[assignment]

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Lightweight row containers (mirror backend schema, no Pydantic dep)
# ---------------------------------------------------------------------------

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# SQL Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS logbook (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
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


# ---------------------------------------------------------------------------
# LogbookClient
# ---------------------------------------------------------------------------

class LogbookClient:
    """Offline-first logbook persistence with optional remote sync.

    Usage::

        client = LogbookClient.get_instance()
        await client.init()
        logbook_id = await client.get_or_create_logbook("user@example.com")
        entry_id = await client.create_entry(logbook_id, title="My entry")
    """

    _instance: LogbookClient | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None
        self._db_path = Path.home() / ".lucid" / "logbook.db"
        self._server_url: str | None = None
        self._enabled = True
        self._offline_only = False
        self._initialized = False

    # -- singleton --

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

    # -- lifecycle --

    async def init(self) -> None:
        """Open (or create) the local database and apply the schema."""
        if self._initialized:
            return
        if aiosqlite is None:
            logger.warning("aiosqlite not installed – logbook persistence disabled")
            return

        self._load_preferences()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        self._initialized = True
        logger.info("LogbookClient initialised (db={})", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
        self._initialized = False

    def _load_preferences(self) -> None:
        """Read settings from PreferencesManager (best-effort)."""
        try:
            from lucid.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
            self._enabled = prefs.get("logbook_enabled", True)
            self._server_url = prefs.get("logbook_url", None)
            self._offline_only = prefs.get("logbook_offline_only", False)
        except Exception:
            logger.debug("Could not load logbook preferences, using defaults")

    # -- helpers --

    def _ensure_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("LogbookClient not initialised – call await init() first")
        return self._db

    # -- Logbook CRUD --

    async def get_or_create_logbook(self, user_id: str) -> str:
        """Return the logbook id for *user_id*, creating one if needed."""
        db = self._ensure_db()
        async with db.execute(
            "SELECT id FROM logbook WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row["id"]

        logbook_id = _uuid()
        await db.execute(
            "INSERT INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)",
            (logbook_id, user_id, _now()),
        )
        await db.commit()
        logger.info("Created logbook {} for user {}", logbook_id, user_id)
        return logbook_id

    # -- Entry CRUD --

    async def create_entry(
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
        await db.execute(
            "INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (eid, logbook_id, title, json.dumps(tags or []), now, now),
        )
        await db.commit()
        return eid

    async def list_entries(
        self, logbook_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        db = self._ensure_db()
        async with db.execute(
            "SELECT * FROM entry WHERE logbook_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (logbook_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        db = self._ensure_db()
        async with db.execute("SELECT * FROM entry WHERE id = ?", (entry_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_entry(
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
        await db.execute(
            f"UPDATE entry SET {', '.join(parts)} WHERE id = ?", params
        )
        await db.commit()

    # -- Fragment CRUD --

    async def add_fragment(
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
            async with db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM fragment WHERE entry_id = ?",
                (entry_id,),
            ) as cur:
                row = await cur.fetchone()
                position = row[0] if row else 0

        await db.execute(
            "INSERT INTO fragment (id, entry_id, position, kind, subtype, content, data, "
            "created_at, updated_at, sync_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (fid, entry_id, position, kind, subtype, content, json.dumps(data) if data else None, now, now),
        )
        await db.commit()
        return fid

    async def update_fragment(
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
        await db.execute(
            f"UPDATE fragment SET {', '.join(parts)} WHERE id = ?", params
        )
        await db.commit()

    async def delete_fragment(self, fragment_id: str) -> None:
        db = self._ensure_db()
        await db.execute("DELETE FROM fragment WHERE id = ?", (fragment_id,))
        await db.commit()

    async def list_fragments(self, entry_id: str) -> list[dict[str, Any]]:
        db = self._ensure_db()
        async with db.execute(
            "SELECT * FROM fragment WHERE entry_id = ? ORDER BY position",
            (entry_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # -- Sync --

    async def sync_to_server(self) -> int:
        """Push locally-pending rows to the remote server. Returns count pushed."""
        if self._offline_only or not self._server_url or httpx is None:
            return 0

        db = self._ensure_db()
        pushed = 0
        try:
            async with httpx.AsyncClient(base_url=self._server_url, timeout=10) as client:
                # Push pending entries
                async with db.execute(
                    "SELECT * FROM entry WHERE sync_status = 'pending'"
                ) as cur:
                    rows = await cur.fetchall()
                for row in rows:
                    r = dict(row)
                    r["tags"] = json.loads(r["tags"]) if isinstance(r["tags"], str) else r["tags"]
                    resp = await client.put(f"/api/entries/{r['id']}", json=r)
                    if resp.is_success:
                        await db.execute(
                            "UPDATE entry SET sync_status = 'synced' WHERE id = ?", (r["id"],)
                        )
                        pushed += 1

                # Push pending fragments
                async with db.execute(
                    "SELECT * FROM fragment WHERE sync_status = 'pending'"
                ) as cur:
                    rows = await cur.fetchall()
                for row in rows:
                    r = dict(row)
                    if r.get("data") and isinstance(r["data"], str):
                        r["data"] = json.loads(r["data"])
                    resp = await client.put(f"/api/fragments/{r['id']}", json=r)
                    if resp.is_success:
                        await db.execute(
                            "UPDATE fragment SET sync_status = 'synced' WHERE id = ?", (r["id"],)
                        )
                        pushed += 1

                await db.commit()
        except Exception as exc:
            logger.warning("Sync to server failed: {}", exc)
        return pushed

    async def sync_from_server(self) -> int:
        """Pull entries/fragments from the remote server. Returns count pulled."""
        if self._offline_only or not self._server_url or httpx is None:
            return 0

        pulled = 0
        db = self._ensure_db()
        try:
            async with httpx.AsyncClient(base_url=self._server_url, timeout=10) as client:
                resp = await client.get("/api/entries")
                if resp.is_success:
                    for entry in resp.json():
                        await db.execute(
                            "INSERT OR REPLACE INTO entry "
                            "(id, logbook_id, title, tags, created_at, updated_at, sync_status) "
                            "VALUES (?, ?, ?, ?, ?, ?, 'synced')",
                            (
                                entry["id"], entry["logbook_id"], entry.get("title"),
                                json.dumps(entry.get("tags", [])),
                                entry["created_at"], entry["updated_at"],
                            ),
                        )
                        pulled += 1
                    await db.commit()
        except Exception as exc:
            logger.warning("Sync from server failed: {}", exc)
        return pulled

    # -- Qt-friendly wrappers --

    def schedule_sync(self, delay_ms: int = 5000) -> None:
        """Schedule a background sync via QTimer (safe from Qt event loop)."""
        QTimer.singleShot(delay_ms, self._run_sync)

    def _run_sync(self) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._do_sync())
            else:
                loop.run_until_complete(self._do_sync())
        except RuntimeError:
            logger.debug("No event loop available for sync")

    async def _do_sync(self) -> None:
        pushed = await self.sync_to_server()
        pulled = await self.sync_from_server()
        if pushed or pulled:
            logger.info("Sync complete: {} pushed, {} pulled", pushed, pulled)
