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

from PySide6.QtCore import QTimer

from lucid.auth.httpx_auth import SessionAuth
from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture, thread_manager

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

CREATE TABLE IF NOT EXISTS image_sync (
    image_id    TEXT PRIMARY KEY,
    local_path  TEXT NOT NULL,
    sync_status TEXT NOT NULL DEFAULT 'pending_upload'
);

CREATE INDEX IF NOT EXISTS idx_entry_logbook ON entry(logbook_id);
CREATE INDEX IF NOT EXISTS idx_fragment_entry ON fragment(entry_id);
"""


def _mime_from_ext(ext: str) -> str:
    """Map file extension to MIME type."""
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}.get(
        ext.lower(), "image/png"
    )


_MIME_TO_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}


def _run_sync(db_path: str, server_url: str, auth_token: str | None = None, user_id: str | None = None) -> tuple[int, int]:
    """Run logbook sync (push pending → pull remote). Returns (pushed, pulled).

    Designed to run inside a QThreadFuture — pure function, no Qt objects.
    """
    if httpx is None:
        return (0, 0)

    pushed = pulled = 0
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    # Use proxy settings if configured
    client_kwargs: dict[str, Any] = {"base_url": server_url, "timeout": 10}
    # Use auth class that reads fresh token per request (same pattern as Tiled)
    client_kwargs["auth"] = SessionAuth(user_id=user_id)
    try:
        from lucid.ui.preferences.proxy_settings import ProxySettingsProvider
        proxy_url = ProxySettingsProvider.should_use_proxy_for_url(server_url)
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
            logger.debug("Logbook sync using proxy: {}", proxy_url)
    except Exception:
        pass

    try:
        with httpx.Client(**client_kwargs) as client:
            # ── Phase 1: Push images (before metadata so server has file before fragment references it)
            try:
                cursor = db.execute(
                    "SELECT image_id, local_path FROM image_sync WHERE sync_status = 'pending_upload'"
                )
                for image_id, local_path in cursor.fetchall():
                    path = Path(local_path)
                    if not path.exists():
                        db.execute(
                            "UPDATE image_sync SET sync_status = 'synced' WHERE image_id = ?",
                            (image_id,),
                        )
                        continue
                    try:
                        with open(path, "rb") as f:
                            resp = client.post(
                                "/logbook/images",
                                files={"file": (path.name, f, _mime_from_ext(path.suffix))},
                            )
                        if resp.status_code == 201:
                            db.execute(
                                "UPDATE image_sync SET sync_status = 'synced' WHERE image_id = ?",
                                (image_id,),
                            )
                            pushed += 1
                    except Exception as exc:
                        logger.debug("Image upload failed for {}: {}", image_id, exc)
                db.commit()
            except Exception as exc:
                logger.debug("Image push phase failed: {}", exc)
            # Push pending entries (PUT to update, POST to create if 404)
            for row in db.execute("SELECT * FROM entry WHERE sync_status = 'pending'"):
                r = dict(row)
                r["tags"] = json.loads(r["tags"]) if isinstance(r["tags"], str) else r["tags"]
                try:
                    resp = client.put(f"/logbook/entries/{r['id']}", json=r)
                    if resp.status_code == 404:
                        resp = client.post("/logbook/entries", json={
                            "id": r["id"],
                            "title": r.get("title"),
                            "tags": r.get("tags", []),
                        })
                    if not resp.is_success:
                        logger.warning("Entry sync failed ({}): {}", resp.status_code, resp.text[:200])
                    if resp.is_success:
                        db.execute("UPDATE entry SET sync_status = 'synced' WHERE id = ?", (r["id"],))
                        pushed += 1
                except Exception:
                    pass

            # Push pending fragments (PUT to update, POST to create if 404)
            for row in db.execute("SELECT * FROM fragment WHERE sync_status = 'pending'"):
                r = dict(row)
                logger.debug("Syncing fragment id={} (type={}, len={})", r["id"], type(r["id"]).__name__, len(str(r["id"])))
                if r.get("data") and isinstance(r["data"], str):
                    r["data"] = json.loads(r["data"])
                try:
                    resp = client.put(f"/logbook/fragments/{r['id']}", json=r)
                    if resp.status_code == 404:
                        resp = client.post(f"/logbook/entries/{r['entry_id']}/fragments", json={
                            "id": r["id"],
                            "kind": r.get("kind", "text"),
                            "subtype": r.get("subtype"),
                            "content": r.get("content", ""),
                            "data": r.get("data"),
                            "position": r.get("position", 0),
                        })
                    if not resp.is_success:
                        logger.warning("Fragment sync failed ({}): {}", resp.status_code, resp.text[:200])
                    if resp.is_success:
                        db.execute("UPDATE fragment SET sync_status = 'synced' WHERE id = ?", (r["id"],))
                        pushed += 1
                except Exception:
                    pass

            db.commit()

            # ── Pull: fetch server entries and upsert locally ─────
            try:
                resp = client.get("/logbook/entries")
                if resp.is_success:
                    remote_entries = resp.json()
                    for re in remote_entries:
                        eid = str(re["id"])
                        local = db.execute("SELECT id, updated_at FROM entry WHERE id = ?", (eid,)).fetchone()
                        logbook_id = str(re["logbook_id"])

                        # Ensure logbook row exists locally
                        if not db.execute("SELECT id FROM logbook WHERE id = ?", (logbook_id,)).fetchone():
                            db.execute(
                                "INSERT OR IGNORE INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)",
                                (logbook_id, user_id or "unknown", re.get("created_at", _now())),
                            )

                        if local is None:
                            db.execute(
                                "INSERT INTO entry (id, logbook_id, title, tags, created_at, updated_at, sync_status) "
                                "VALUES (?, ?, ?, ?, ?, ?, 'synced')",
                                (eid, logbook_id, re.get("title"),
                                 json.dumps(re.get("tags", [])),
                                 re.get("created_at", _now()), re.get("updated_at", _now())),
                            )
                            pulled += 1
                        elif local["updated_at"] < re.get("updated_at", ""):
                            cur = db.execute("SELECT sync_status FROM entry WHERE id = ?", (eid,)).fetchone()
                            if cur and cur["sync_status"] != "pending":
                                db.execute(
                                    "UPDATE entry SET title = ?, tags = ?, updated_at = ?, sync_status = 'synced' WHERE id = ?",
                                    (re.get("title"), json.dumps(re.get("tags", [])), re.get("updated_at"), eid),
                                )
                                pulled += 1

                        # Pull fragments for this entry
                        for rf in re.get("fragments", []):
                            fid = str(rf["id"])
                            local_frag = db.execute("SELECT id, updated_at, sync_status FROM fragment WHERE id = ?", (fid,)).fetchone()
                            if local_frag is None:
                                db.execute(
                                    "INSERT INTO fragment (id, entry_id, position, kind, subtype, content, data, created_at, updated_at, sync_status) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced')",
                                    (fid, eid, rf.get("position", 0), rf.get("kind", "text"),
                                     rf.get("subtype"), rf.get("content", ""),
                                     json.dumps(rf.get("data")) if rf.get("data") else None,
                                     rf.get("created_at", _now()), rf.get("updated_at", _now())),
                                )
                                pulled += 1
                            elif local_frag["sync_status"] != "pending" and local_frag["updated_at"] < rf.get("updated_at", ""):
                                db.execute(
                                    "UPDATE fragment SET position = ?, content = ?, data = ?, updated_at = ?, sync_status = 'synced' WHERE id = ?",
                                    (rf.get("position", 0), rf.get("content", ""),
                                     json.dumps(rf.get("data")) if rf.get("data") else None,
                                     rf.get("updated_at"), fid),
                                )
                                pulled += 1

                    db.commit()
            except Exception as exc:
                logger.warning("Pull sync failed: {}", exc)

            # ── Phase 4: Pull images (download missing image files after metadata pull)
            try:
                cursor = db.execute(
                    "SELECT id, data FROM fragment WHERE kind = 'image'"
                )
                image_dir = Path.home() / ".lucid" / "logbook" / "images"
                image_dir.mkdir(parents=True, exist_ok=True)

                for _frag_id, data_json in cursor.fetchall():
                    frag_data = json.loads(data_json) if data_json else {}
                    img_id = frag_data.get("image_id")
                    if not img_id:
                        continue

                    # Check if already synced
                    existing = db.execute(
                        "SELECT sync_status FROM image_sync WHERE image_id = ?", (img_id,)
                    ).fetchone()
                    if existing and existing["sync_status"] == "synced":
                        continue

                    # Check if file exists on disk
                    mime_type = frag_data.get("mime_type", "image/png")
                    ext = _MIME_TO_EXT.get(mime_type, ".png")
                    local_path = image_dir / f"{img_id}{ext}"

                    if local_path.exists():
                        db.execute(
                            "INSERT OR REPLACE INTO image_sync (image_id, local_path, sync_status) VALUES (?, ?, 'synced')",
                            (img_id, str(local_path)),
                        )
                        continue

                    # Download from server
                    try:
                        resp = client.get(f"/logbook/images/{img_id}")
                        if resp.status_code == 200:
                            local_path.write_bytes(resp.content)
                            db.execute(
                                "INSERT OR REPLACE INTO image_sync (image_id, local_path, sync_status) VALUES (?, ?, 'synced')",
                                (img_id, str(local_path)),
                            )
                            pulled += 1
                    except Exception as exc:
                        logger.debug("Image download failed for {}: {}", img_id, exc)
                        db.execute(
                            "INSERT OR IGNORE INTO image_sync (image_id, local_path, sync_status) VALUES (?, ?, 'pending_download')",
                            (img_id, str(local_path)),
                        )
                db.commit()
            except Exception as exc:
                logger.debug("Image pull phase failed: {}", exc)
    finally:
        db.close()

    return (pushed, pulled)


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
        self._sync_timer: QTimer | None = None
        self._on_pull_callback: callable | None = None
        self._on_sync_error_callback: callable | None = None
        self._on_sync_restored_callback: callable | None = None
        self._on_entry_created_callback: callable | None = None
        self._sync_failed = False

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
        self.schedule_sync()

    def purge_synced(self) -> None:
        """Remove all locally-synced data, keeping only pending (unsynced) rows.

        Called on close/logout so the local DB stays lean — it's a
        temporary buffer, not a growing archive.
        """
        db = self._ensure_db()
        # Delete synced fragments first (FK order)
        db.execute("DELETE FROM fragment WHERE sync_status = 'synced'")
        # Delete synced entries that have no remaining fragments
        db.execute(
            "DELETE FROM entry WHERE sync_status = 'synced' "
            "AND id NOT IN (SELECT DISTINCT entry_id FROM fragment)"
        )
        # Delete logbooks with no remaining entries
        db.execute(
            "DELETE FROM logbook WHERE id NOT IN (SELECT DISTINCT logbook_id FROM entry)"
        )
        db.commit()
        db.execute("VACUUM")
        logger.info("Purged synced content from local logbook DB")

    def close(self) -> None:
        if self._db:
            try:
                self.purge_synced()
            except Exception as exc:
                logger.warning("Failed to purge synced data on close: {}", exc)
            self._db.close()
            self._db = None
        self._initialized = False

    def _load_preferences(self) -> None:
        try:
            from lucid.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
            self._server_url = prefs.get("logbook_url", None) or "http://bcglucidlogbook.dhcp.lbl.gov"
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
        if self._on_entry_created_callback:
            cb = self._on_entry_created_callback
            QTimer.singleShot(0, lambda: cb(eid, logbook_id))
        self.schedule_sync()
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
        self.schedule_sync()

    def delete_entry(self, entry_id: str) -> None:
        db = self._ensure_db()
        db.execute("DELETE FROM fragment WHERE entry_id = ?", (entry_id,))
        db.execute("DELETE FROM entry WHERE id = ?", (entry_id,))
        db.commit()
        self.schedule_sync()

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
        self.schedule_sync()
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
        self.schedule_sync()

    def reorder_fragments(self, entry_id: str, fragment_ids: list[str]) -> None:
        """Update fragment positions to match the given order."""
        db = self._ensure_db()
        for pos, fid in enumerate(fragment_ids):
            db.execute(
                "UPDATE fragment SET position = ?, sync_status = 'pending' WHERE id = ? AND entry_id = ?",
                (pos, fid, entry_id),
            )
        db.commit()
        self.schedule_sync()

    def delete_fragment(self, fragment_id: str) -> None:
        db = self._ensure_db()
        db.execute("DELETE FROM fragment WHERE id = ?", (fragment_id,))
        db.commit()
        self.schedule_sync()

    def list_fragments(self, entry_id: str) -> list[dict[str, Any]]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM fragment WHERE entry_id = ? ORDER BY position",
            (entry_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Images ───────────────────────────────────────────────────

    _MIME_TO_EXT = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
    }
    _EXT_TO_MIME = {v: k for k, v in _MIME_TO_EXT.items()}

    @property
    def _image_dir(self) -> Path:
        """Local image storage directory."""
        d = Path.home() / ".lucid" / "logbook" / "images"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_image_locally(self, image_id: str, data: bytes, mime_type: str) -> Path:
        """Save image bytes to local storage, return the file path."""
        ext = self._MIME_TO_EXT.get(mime_type, ".png")
        path = self._image_dir / f"{image_id}{ext}"
        path.write_bytes(data)
        return path

    def _get_local_image_path(self, image_id: str) -> Path | None:
        """Find a locally stored image by ID, or None if not present."""
        for ext in self._MIME_TO_EXT.values():
            path = self._image_dir / f"{image_id}{ext}"
            if path.exists():
                return path
        return None

    def add_image(
        self,
        image: Any,
        caption: str = "",
        subtype: str = "clipboard",
        entry_id: str | None = None,
    ) -> str:
        """Add an image fragment to the logbook.

        Args:
            image: Image as raw bytes, file path (str/Path), QImage, or QPixmap.
            caption: Optional caption text.
            subtype: Fragment subtype for styling.
            entry_id: Target entry ID. None = current entry (caller must provide).

        Returns:
            The new fragment ID.
        """
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage, QPixmap

        # Normalize input to bytes + mime_type
        if isinstance(image, QPixmap):
            image = image.toImage()
        if isinstance(image, QImage):
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buf, "PNG")
            data = bytes(buf.data())
            mime_type = "image/png"
        elif isinstance(image, (str, Path)):
            path = Path(image)
            data = path.read_bytes()
            mime_type = self._EXT_TO_MIME.get(path.suffix.lower(), "image/png")
        elif isinstance(image, bytes):
            data = image
            if data[:2] == b"\xff\xd8":
                mime_type = "image/jpeg"
            elif data[:4] == b"GIF8":
                mime_type = "image/gif"
            else:
                mime_type = "image/png"
        else:
            raise TypeError(f"Unsupported image type: {type(image)}")

        image_id = _uuid()

        # Save to local disk
        local_path = self._save_image_locally(image_id, data, mime_type)

        # Get image dimensions
        qimg = QImage()
        qimg.loadFromData(data)
        width = qimg.width() if not qimg.isNull() else 0
        height = qimg.height() if not qimg.isNull() else 0

        ext = self._MIME_TO_EXT.get(mime_type, ".png")
        filename = f"{image_id}{ext}"

        if entry_id is None:
            raise ValueError("entry_id is required (no current entry tracking yet)")

        # Create image fragment in local DB
        fragment_id = self.add_fragment(
            entry_id,
            kind="image",
            subtype=subtype,
            content=caption,
            data={
                "image_id": image_id,
                "filename": filename,
                "mime_type": mime_type,
                "width": width,
                "height": height,
                "size_bytes": len(data),
            },
        )

        # Track for sync
        db = self._ensure_db()
        db.execute(
            "INSERT OR REPLACE INTO image_sync (image_id, local_path, sync_status) VALUES (?, ?, ?)",
            (image_id, str(local_path), "pending_upload"),
        )
        db.commit()

        self.schedule_sync()
        return fragment_id

    # ── Sync ──────────────────────────────────────────────────────

    def set_on_pull_callback(self, callback: callable) -> None:
        """Register a callback to be invoked when data is pulled from the server."""
        self._on_pull_callback = callback

    def set_on_entry_created_callback(self, callback: callable) -> None:
        """Register a callback invoked when an entry is created locally.

        The callback receives ``(entry_id, logbook_id)``.
        """
        self._on_entry_created_callback = callback

    def schedule_sync(self) -> None:
        """Debounce sync — waits 2s after last mutation before starting."""
        if self._offline_only or not self._server_url:
            return
        if self._sync_timer is None:
            self._sync_timer = QTimer()
            self._sync_timer.setSingleShot(True)
            self._sync_timer.timeout.connect(self._do_sync)
        # Restart the 2s debounce timer on each call
        self._sync_timer.start(2000)

    @staticmethod
    def _is_guest_user() -> bool:
        """Check if the current user is a guest (no sync for guests)."""
        try:
            from lucid.auth.policy import Role
            from lucid.auth.session import SessionManager
            user = SessionManager.get_instance().current_user
            return user.highest_role == Role.GUEST
        except Exception:
            return False

    def _do_sync(self) -> None:
        """Actually start the background sync via QThreadFuture."""
        # Don't sync for guest users — their data is local-only
        if self._is_guest_user():
            logger.debug("Skipping logbook sync for guest user")
            return

        # If a sync is already running, re-schedule to pick up new changes after
        existing = thread_manager.get_by_key("logbook-sync")
        if existing and existing.running:
            self._sync_timer.start(2000)
            return

        # Get auth token and user ID from session manager if available
        auth_token: str | None = None
        user_id: str | None = None
        try:
            from lucid.auth.session import SessionManager
            sm = SessionManager.get_instance()
            session = sm.session
            if session and session.token:
                auth_token = session.token
            user = sm.current_user
            if user and user.username:
                user_id = user.username
        except Exception:
            pass

        QThreadFuture(
            _run_sync,
            str(self._db_path),
            self._server_url,
            auth_token,
            user_id,
            callback_slot=self._on_sync_done,
            except_slot=self._on_sync_error,
            key="logbook-sync",
            name="logbook-sync",
        ).start()

    def _on_sync_done(self, result: tuple[int, int]) -> None:
        pushed, pulled = result
        if pushed or pulled:
            logger.info("Sync complete: {} pushed, {} pulled", pushed, pulled)
        if pulled > 0 and self._on_pull_callback:
            self._on_pull_callback()
        # Connection restored after previous failure
        if self._sync_failed:
            self._sync_failed = False
            if self._on_sync_restored_callback:
                self._on_sync_restored_callback()

    def _on_sync_error(self, exc: Exception) -> None:
        logger.warning("Sync failed: {}", exc)
        if not self._sync_failed:
            self._sync_failed = True
            if self._on_sync_error_callback:
                self._on_sync_error_callback()
