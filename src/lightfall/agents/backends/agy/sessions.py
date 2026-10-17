"""agy session listing / transcript loading (per-spec antigravity-cli store).

``conversation_summaries.db`` and ``brain/<id>/.system_generated/logs/transcript.jsonl``
live under ``<spec home>/.gemini/antigravity-cli``; session homes link there.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from lightfall.agents.backend import SessionInfo, TranscriptEntry
from lightfall.agents.tooling import tool_id
from lightfall.utils.logging import logger

_PREFIX = "lightfall_"
_USER_RE = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.DOTALL)
_DRIVE_RE = re.compile(r"^/[A-Za-z]:")
# agy conversation ids are UUIDs; anything else must not reach a file path.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).casefold()


def _uri_to_path(uri: str) -> str:
    if not uri.startswith("file:"):
        return uri
    p = unquote(urlparse(uri).path)
    return p[1:] if _DRIVE_RE.match(p) else p


def _unquote_json(value: Any) -> str:
    """agy stores some tool args as JSON-encoded strings (``'"lightfall_x"'``)."""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        return decoded if isinstance(decoded, str) else value
    return "" if value is None else str(value)


def _tool_name(call: dict[str, Any]) -> str:
    name = str(call.get("name") or "unknown")
    args = call.get("args")
    if name == "call_mcp_tool" and isinstance(args, dict) and args:
        server = _unquote_json(args.get("ServerName")).removeprefix(_PREFIX)
        tool = _unquote_json(args.get("ToolName"))
        if server and tool:
            return tool_id(server, tool)
    return name


class AgySessionStore:
    def __init__(self, home: Path, workspace: str) -> None:
        self._root = Path(home) / ".gemini" / "antigravity-cli"
        self._workspace = _norm(workspace)

    def _in_workspace(self, raw: str) -> bool:
        try:
            uris = json.loads(raw)
        except (TypeError, ValueError):
            return False
        if not isinstance(uris, list):
            return False
        return any(isinstance(u, str) and _norm(_uri_to_path(u)) == self._workspace for u in uris)

    def list_sessions(self, limit: int = 15) -> list[SessionInfo]:
        db = self._root / "conversation_summaries.db"
        try:
            if not db.is_file():
                return []
            con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT conversation_id, title, preview, last_modified_time, "
                    "workspace_uris, parent_conversation_id FROM conversation_summaries "
                    "ORDER BY last_modified_time DESC"
                ).fetchall()
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("agy list_sessions failed: {}", exc)
            return []
        out: list[SessionInfo] = []
        for cid, title, preview, _mtime, uris, parent in rows:
            if parent or not self._in_workspace(uris):
                continue
            if not (title or preview):
                continue  # summary row created before the first reply: not a session yet
            text = title or preview
            text = (text[:60] + "…") if len(text) > 60 else text
            out.append(SessionInfo(cid, text))
            if len(out) >= limit:
                break
        return out

    def load_transcript(self, session_id: str) -> list[TranscriptEntry]:
        if not _SESSION_ID_RE.match(session_id or ""):
            logger.warning("Ignoring malformed agy session id {!r}", session_id)
            return []
        path = (self._root / "brain" / session_id / ".system_generated" / "logs"
                / "transcript.jsonl")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Could not load agy transcript for {}: {}", session_id, exc)
            return []
        out: list[TranscriptEntry] = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            kind = rec.get("type")
            content = rec.get("content")
            if kind == "USER_INPUT" and isinstance(content, str):
                m = _USER_RE.search(content)
                out.append(TranscriptEntry("user", (m.group(1) if m else content).strip(), []))
            elif kind == "PLANNER_RESPONSE" and isinstance(content, str) and content:
                calls = rec.get("tool_calls")
                names = ([_tool_name(c) for c in calls if isinstance(c, dict)]
                         if isinstance(calls, list) else [])
                out.append(TranscriptEntry("assistant", content, names))
        return out
