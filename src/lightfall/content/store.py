"""This machine's read-only copy of scoped content, one folder per scope.

Layout under the root (``default_content_dir()``)::

    chains.json                  {bind: [facility, beamline:…, branch:…]} last synced per bind
    branch-7.0.1.1/index.json    the listing that is installed (not what the server last said)
    branch-7.0.1.1/plugin/xas_tools@17/…   one folder per revision, never changed once there
    .staging/                    downloads and extractions in progress
    .lock                        held while a process syncs

Readers take each item's folder from index.json, never by listing folders. A new
revision lands beside the old one, so nothing changes under code that is already
loaded (Windows can't rename a folder with open files either); ``prune()`` removes
folders the index no longer names, at a process's first sync.

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

    def item_path(self, scope: str, type_: str, name: str, revision: int) -> Path:
        """The folder holding one revision of an item."""
        if not valid_item(type_, name):
            raise ValueError(f"invalid content item {type_}/{name!r}")
        return self.scope_dir(scope) / type_ / f"{name}@{int(revision)}"

    @property
    def lock_path(self) -> Path:
        return self.root / ".lock"

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
        listing = self._read_listing(self.scope_dir(scope) / "index.json")
        return listing if listing is not None and listing.scope == scope else None

    @staticmethod
    def _read_listing(path: Path) -> ContentListing | None:
        try:
            return ContentListing.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning("Ignoring unreadable content index {}: {}", path, e)
            return None

    def save_listing(self, listing: ContentListing) -> None:
        write_json_atomic(self.scope_dir(listing.scope) / "index.json", listing.to_dict())

    # -- items ------------------------------------------------------------------------

    def install(self, scope: str, item: ContentItem, zip_path: Path) -> None:
        """Unpack ``zip_path`` as ``item``'s revision folder (no-op if already there).

        Raises UnsafeBundle or OSError; nothing is left behind then.
        """
        target = self.item_path(scope, item.type, item.name, item.revision)
        if target.is_dir():
            return  # folders arrive whole, by rename: one that exists is complete
        staged = Path(tempfile.mkdtemp(dir=self._staging(), prefix="new-"))
        try:
            safe_extract(zip_path, staged,
                         max_files=item.file_count if item.file_count is not None
                         else FALLBACK_MAX_FILES,
                         max_bytes=item.uncompressed_size if item.uncompressed_size is not None
                         else FALLBACK_MAX_BYTES)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
        finally:
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)

    def prune(self) -> None:
        """Delete revision folders no index names, and leftovers in ``.staging``.

        Best effort (a folder in use stays until next time). A scope whose index can't
        be read is left alone: without it we can't tell what is current.
        """
        if not self.root.is_dir():
            return
        staging = self._staging()
        for scope_dir in self.root.iterdir():
            if not scope_dir.is_dir() or scope_dir.name.startswith("."):
                continue
            index = scope_dir / "index.json"
            listing = self._read_listing(index)
            if listing is None and index.exists():
                continue
            keep = {(i.type, f"{i.name}@{i.revision}")
                    for i in (listing.items if listing is not None else ())}
            for type_dir in scope_dir.iterdir():
                if not type_dir.is_dir() or type_dir.name not in CONTENT_TYPES:
                    continue
                for folder in type_dir.iterdir():
                    if (type_dir.name, folder.name) in keep:
                        continue
                    # Rename first so a half-finished delete never looks like content.
                    trash = Path(tempfile.mkdtemp(dir=staging, prefix="old-")) / "item"
                    try:
                        os.replace(folder, trash)
                    except OSError as e:
                        logger.debug("Content folder {} in use; pruned later: {}", folder, e)
                        continue
        for leftover in staging.iterdir():
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
            else:
                leftover.unlink(missing_ok=True)

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
