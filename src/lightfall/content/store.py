"""This machine's read-only copy of scoped content, one folder per scope.

Layout under the root (``default_content_dir()``)::

    chains.json                  {bind: [facility, beamline:…, branch:…]} last synced per bind
    branch-7.0.1.1/index.json    the listing that is installed (not what the server last said)
    branch-7.0.1.1/plugin/xas_tools/…
    .staging/                    downloads and extractions in progress

The copy belongs to the machine, not the user: guests read the last synced copy.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from lightfall.content.extract import safe_extract
from lightfall.scopes.cache import write_json_atomic
from lightfall.scopes.client import ContentItem, ContentListing
from lightfall.utils.logging import logger

CONTENT_TYPES = ("plan", "plugin", "agent", "skill")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")  # lightfall_logbook.content_api._NAME
_UNSAFE = re.compile(r"[^A-Za-z0-9._@-]")
# Older servers don't list the declared counts; these are the server's default policy.
FALLBACK_MAX_FILES = 2000
FALLBACK_MAX_BYTES = 200 * 1024**2


def valid_item(type_: str, name: str) -> bool:
    return type_ in CONTENT_TYPES and bool(_NAME.match(name)) and ".." not in name


def default_content_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "lightfall" / "content"


def _is_shared_scope(scope: str) -> bool:
    return scope == "facility" or scope.startswith(("beamline:", "branch:"))


class ContentStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths ------------------------------------------------------------------------

    def scope_dir(self, scope: str) -> Path:
        kind, sep, rest = scope.partition(":")
        stem = f"{kind}-{rest}" if sep else kind
        return self.root / _UNSAFE.sub("_", stem)

    def item_path(self, scope: str, type_: str, name: str) -> Path:
        if not valid_item(type_, name):
            raise ValueError(f"invalid content item {type_}/{name!r}")
        return self.scope_dir(scope) / type_ / name

    def _staging(self) -> Path:
        path = self.root / ".staging"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def new_staging_file(self) -> Path:
        """An empty temp file for a download; the caller deletes it."""
        fd, name = tempfile.mkstemp(dir=self._staging(), prefix="dl-", suffix=".zip")
        os.close(fd)
        return Path(name)

    # -- installed index --------------------------------------------------------------

    def listing(self, scope: str) -> ContentListing | None:
        path = self.scope_dir(scope) / "index.json"
        try:
            listing = ContentListing.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning("Ignoring unreadable content index {}: {}", path, e)
            return None
        return listing if listing.scope == scope else None

    def save_listing(self, listing: ContentListing) -> None:
        write_json_atomic(self.scope_dir(listing.scope) / "index.json", listing.to_dict())

    # -- items ------------------------------------------------------------------------

    def install(self, scope: str, item: ContentItem, zip_path: Path) -> None:
        """Unpack ``zip_path`` as ``item`` and swap it into place.

        Raises UnsafeBundle or OSError; the previous copy is then still in place.
        """
        target = self.item_path(scope, item.type, item.name)
        staged = Path(tempfile.mkdtemp(dir=self._staging(), prefix="new-"))
        try:
            safe_extract(zip_path, staged,
                         max_files=item.file_count if item.file_count is not None
                         else FALLBACK_MAX_FILES,
                         max_bytes=item.uncompressed_size if item.uncompressed_size is not None
                         else FALLBACK_MAX_BYTES)
            target.parent.mkdir(parents=True, exist_ok=True)
            old: Path | None = None
            if target.exists():
                old = Path(tempfile.mkdtemp(dir=self._staging(), prefix="old-")) / "item"
                os.replace(target, old)
            try:
                os.replace(staged, target)
            except OSError:
                if old is not None:
                    os.replace(old, target)
                raise
            if old is not None:
                shutil.rmtree(old.parent, ignore_errors=True)
        finally:
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)

    def remove(self, scope: str, type_: str, name: str) -> None:
        path = self.item_path(scope, type_, name)
        if path.exists():
            shutil.rmtree(path)

    # -- chains (which scopes each binding reads) ------------------------------------

    def _chains(self) -> dict[str, list[str]]:
        path = self.root / "chains.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning("Ignoring unreadable {}: {}", path, e)
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): [str(s) for s in v] for k, v in raw.items() if isinstance(v, list)}

    def save_chain(self, bind: str, scopes: Sequence[str]) -> None:
        chains = self._chains()
        chains[bind] = [s for s in scopes if _is_shared_scope(s)]
        write_json_atomic(self.root / "chains.json", chains)

    def chain(self, bind: str) -> tuple[str, ...]:
        return tuple(self._chains().get(bind, ()))

    def sources(self, bind: str) -> list[tuple[str, Path]]:
        """``(scope, folder)`` for each synced scope of ``bind``, most general first."""
        return [(s, self.scope_dir(s)) for s in self.chain(bind) if self.scope_dir(s).is_dir()]
