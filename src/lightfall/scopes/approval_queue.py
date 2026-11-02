"""Scoped writes waiting for a facility admin to approve their setting key.

The server refuses a write above user scope of a key it hasn't approved
(``key_pending``). The write is kept here, one file per identity next to the write
journal, and sent again once the key is approved. Latest value wins per (scope, key);
a value of None is a queued remove.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lightfall.scopes.cache import write_json_atomic
from lightfall.utils.logging import logger

_UNSAFE = re.compile(r"[^A-Za-z0-9._@-]")


def approval_queue_path(directory: Path, identity: str) -> Path:
    return Path(directory) / f"approvals-{_UNSAFE.sub('_', identity)}.json"


class ApprovalQueue:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._entries = self._load()

    def _load(self) -> dict[tuple[str, str], Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning("Ignoring unreadable approval queue {}: {}", self._path, e)
            return {}
        if not isinstance(raw, list):
            return {}
        return {
            (str(e["scope"]), str(e["key"])): e.get("value")
            for e in raw if isinstance(e, dict) and e.get("scope") and e.get("key")
        }

    def _save(self) -> None:
        write_json_atomic(self._path, [
            {"scope": scope, "key": key, "value": value}
            for (scope, key), value in self._entries.items()
        ])

    def add(self, scope: str, key: str, value: Any) -> None:
        """Queue (or replace) the write of ``key`` at ``scope``; None queues a remove."""
        self._entries[(scope, key)] = value
        self._save()

    def remove(self, scope: str, key: str) -> None:
        if self._entries.pop((scope, key), _ABSENT) is not _ABSENT:
            self._save()

    def entries(self) -> dict[tuple[str, str], Any]:
        return dict(self._entries)

    def keys(self) -> set[str]:
        """The setting keys with a queued write at any scope."""
        return {key for _scope, key in self._entries}


_ABSENT = object()
