"""JSON disk cache of scope snapshots and chains (spec section 5: before-login and offline reads)."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from lightfall.scopes.client import ChainInfo, Snapshot
from lightfall.utils.logging import logger

_UNSAFE = re.compile(r"[^A-Za-z0-9._@-]")


class SnapshotCache:
    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    def path_for(self, name: str) -> Path:
        kind, sep, rest = name.partition(":")
        stem = f"{kind}-{rest}" if sep else kind
        return self._dir / f"{_UNSAFE.sub('_', stem)}.json"

    def _read(self, name: str) -> tuple[dict[str, Any], float] | None:
        path = self.path_for(name)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return dict(raw["data"]), float(raw["saved_at"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning("Ignoring unreadable scope cache {}: {}", path, e)
            return None

    def _write(self, name: str, data: dict[str, Any]) -> None:
        """Best effort: a cache that can't be written must not fail a good load."""
        try:
            self._write_atomic(name, data)
        except OSError as e:
            logger.warning("Could not write scope cache {}: {}", self.path_for(name), e)

    def _write_atomic(self, name: str, data: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(name)
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"saved_at": time.time(), "data": data}, f)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def load_snapshot(self, scope: str) -> tuple[Snapshot, float] | None:
        got = self._read(scope)
        if got is None:
            return None
        data, saved_at = got
        try:
            snap = Snapshot.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None
        return (snap, saved_at) if snap.scope == scope else None

    def save_snapshot(self, snap: Snapshot) -> None:
        self._write(snap.scope, snap.to_dict())

    # Chains and key lists are per user: they carry that user's scope, roles and
    # (for maintainers) the full key list, and consoles often share one OS account.
    def load_chain(self, bind: str, identity: str) -> tuple[ChainInfo, float] | None:
        got = self._read(f"chain:{identity}:{bind}")
        if got is None:
            return None
        data, saved_at = got
        try:
            return ChainInfo.from_dict(data), saved_at
        except (KeyError, TypeError, ValueError):
            return None

    def save_chain(self, bind: str, info: ChainInfo, identity: str) -> None:
        self._write(f"chain:{identity}:{bind}", info.to_dict())

    def load_keys(self, identity: str) -> list[dict[str, Any]] | None:
        got = self._read(f"setting-keys:{identity}")
        if got is None:
            return None
        records = got[0].get("records")
        return list(records) if isinstance(records, list) else None

    def save_keys(self, records: list[dict[str, Any]], identity: str) -> None:
        self._write(f"setting-keys:{identity}", {"records": records})
