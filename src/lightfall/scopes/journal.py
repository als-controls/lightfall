"""Last-write-wins queue of user-scope writes made while the server was unreachable."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lightfall.scopes.cache import write_json_atomic
from lightfall.utils.logging import logger

_UNSAFE = re.compile(r"[^A-Za-z0-9._@-]")
_OPS = ("set", "delete")


def journal_path(directory: Path, identity: str) -> Path:
    return Path(directory) / f"journal-{_UNSAFE.sub('_', identity)}.json"


class WriteJournal:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._entries = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning("Ignoring unreadable settings journal {}: {}", self._path, e)
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(k): {"op": v["op"], "value": v.get("value")}
            for k, v in raw.items() if isinstance(v, dict) and v.get("op") in _OPS
        }

    def record(self, key: str, op: str, value: Any = None) -> bool:
        """Queue a write. Returns False (nothing changed) for a non-JSON value."""
        if op not in _OPS:
            raise ValueError(f"unknown journal op {op!r}")
        entry = {"op": op, "value": None if op == "delete" else value}
        try:
            json.dumps(entry, allow_nan=False)
        except (TypeError, ValueError) as e:
            logger.warning("Not journaling {}: value is not JSON ({})", key, e)
            return False
        self._entries[key] = entry
        write_json_atomic(self._path, self._entries)
        return True

    def discard_if(self, key: str, op: str, value: Any) -> None:
        """Discard ``key`` only if its entry is still this write (not a later one)."""
        entry = self._entries.get(key)
        if entry is not None and entry["op"] == op and (op == "delete" or entry["value"] == value):
            self.discard(key)

    def discard(self, key: str) -> None:
        if self._entries.pop(key, None) is not None:
            write_json_atomic(self._path, self._entries)

    def entries(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._entries.items()}

    def __len__(self) -> int:
        return len(self._entries)
