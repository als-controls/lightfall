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

    def record(self, key: str, op: str, value: Any = None) -> None:
        if op not in _OPS:
            raise ValueError(f"unknown journal op {op!r}")
        self._entries[key] = {"op": op, "value": None if op == "delete" else value}
        write_json_atomic(self._path, self._entries)

    def discard(self, key: str) -> None:
        if self._entries.pop(key, None) is not None:
            write_json_atomic(self._path, self._entries)

    def entries(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._entries.items()}

    def __len__(self) -> int:
        return len(self._entries)
