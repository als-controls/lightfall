"""Bundling local items for publishing to a shared scope.

A bundle is a zip whose layout round-trips through ``lightfall.content.sources``:
plan ``<name>.py``; plugin ``<name>.py`` or the package folder's contents; agent one
``<name>.md``; skill the folder's contents with ``SKILL.md`` at the root. The zip is built
once and hashed; the bytes that were hashed (and shown to the user) are the bytes uploaded.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lightfall.agents.spec import parse_agent_file
from lightfall.content.store import valid_item
from lightfall.scopes.client import ContentPolicy
from lightfall.utils.git_tracker import GitTracker
from lightfall.utils.logging import logger

MAX_MESSAGE_CHARS = 1000
MAX_AUTHOR_CHARS = 256
MAX_SOURCE_BYTES = 2048

_EXCLUDED_DIRS = {"__pycache__", ".git"}
_EXCLUDED_FILES = {".DS_Store", "Thumbs.db"}
_DRAFTS = "_drafts"
_MB = 1024 * 1024


@dataclass(frozen=True)
class LocalItem:
    type: str
    name: str
    path: Path  # plan .py; plugin .py or folder; agent .md; skill folder


@dataclass(frozen=True)
class Bundle:
    path: Path
    sha256: str
    size: int
    file_count: int
    uncompressed_size: int
    files: tuple[str, ...]


def default_root() -> Path:
    return Path.home() / "lightfall"


def local_items(root: Path | None = None) -> list[LocalItem]:
    """The user's local plans, plugins, agents and skills under ``root`` (default ~/lightfall)."""
    root = root if root is not None else default_root()
    found: list[LocalItem] = []

    def add(type_: str, name: str, path: Path) -> None:
        if valid_item(type_, name):
            found.append(LocalItem(type_, name, path))
        else:
            logger.warning("Skipping local {} {!r} at {}: not a publishable name",
                           type_, name, path)

    def entries(sub: str) -> list[Path]:
        d = root / sub
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if not p.name.startswith("_"))

    for p in entries("plans"):
        if p.is_file() and p.suffix == ".py":
            add("plan", p.stem, p)
    for p in entries("plugins"):
        if p.is_file() and p.suffix == ".py":
            add("plugin", p.stem, p)
        elif p.is_dir() and (p / "__init__.py").is_file():
            add("plugin", p.name, p)
    for p in entries("agents"):
        if p.is_file() and p.suffix == ".md":
            try:
                name = parse_agent_file(p, "local").name
            except Exception as e:
                logger.warning("Skipping local agent file {}: {}", p, e)
                continue
            add("agent", name, p)
    for p in entries("skills"):
        if p.is_dir() and (p / "SKILL.md").is_file():
            add("skill", p.name, p)
    return found


def _walk(folder: Path, skip_drafts: bool) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _EXCLUDED_DIRS
            and not (skip_drafts and here == folder and d == _DRAFTS)
            and not (here / d).is_symlink()
        )
        for fn in sorted(filenames):
            p = here / fn
            if fn in _EXCLUDED_FILES or fn.endswith(".pyc") or p.is_symlink():
                continue
            out.append((p.relative_to(folder).as_posix(), p))
    return out


def _members(item: LocalItem) -> list[tuple[str, Path]]:
    if item.type in ("plan", "plugin") and item.path.is_file():
        return [(f"{item.name}.py", item.path)]
    if item.type == "agent" and item.path.is_file():
        return [(f"{item.name}.md", item.path)]
    if item.type in ("plugin", "skill") and item.path.is_dir():
        return _walk(item.path, skip_drafts=item.type == "skill")
    raise ValueError(f"cannot bundle {item.type} {item.name!r} from {item.path}")


def build_bundle(item: LocalItem, dest_dir: Path) -> Bundle:
    """Zip ``item`` into ``dest_dir`` and describe the result (hash, sizes, file list)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    members = _members(item)
    zip_path = dest_dir / f"{item.type}-{item.name}.zip"
    uncompressed = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, src in members:
            data = src.read_bytes()
            uncompressed += len(data)
            zf.writestr(arcname, data)
    raw = zip_path.read_bytes()
    return Bundle(
        path=zip_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
        file_count=len(members),
        uncompressed_size=uncompressed,
        files=tuple(a for a, _ in members),
    )


def _human(n: int) -> str:
    if n >= _MB:
        return f"{n / _MB:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _limit(n: int) -> str:
    """Like _human but a whole number of MB drops the decimal ("50 MB")."""
    if n >= _MB and n % _MB == 0:
        return f"{n // _MB} MB"
    return _human(n)


def policy_problems(bundle: Bundle, policy: ContentPolicy) -> list[str]:
    """One plain line per limit the bundle exceeds (empty when it fits)."""
    problems: list[str] = []
    if bundle.size > policy.max_bundle_bytes:
        problems.append(f"{_human(bundle.size)} compressed; "
                        f"the limit is {_limit(policy.max_bundle_bytes)}")
    if bundle.uncompressed_size > policy.max_uncompressed_bytes:
        problems.append(f"{_human(bundle.uncompressed_size)} uncompressed; "
                        f"the limit is {_limit(policy.max_uncompressed_bytes)}")
    if bundle.file_count > policy.max_files:
        problems.append(f"{bundle.file_count:,} files; the limit is {policy.max_files:,}")
    return problems


def _source_size(source: dict[str, str]) -> int:
    return len(json.dumps(source, ensure_ascii=False).encode("utf-8"))


def _fit_source(source: dict[str, str]) -> dict[str, str]:
    """Shorten ``source['path']`` from the left (``...tail``) until the JSON fits 2048 bytes."""
    out = dict(source)
    path = source["path"]
    while path and _source_size(out) > MAX_SOURCE_BYTES:
        excess = _source_size(out) - MAX_SOURCE_BYTES
        path = path[max(1, excess // 2):]
        out["path"] = "..." + path
    return out


def make_manifest(item: LocalItem, bundle: Bundle, *, message: str, author: str | None,
                  agent: bool) -> dict[str, Any]:
    """The upload manifest.

    Raises ValueError for a message over 1000 or an author over 256 characters. A source
    path that would push the source JSON over 2048 bytes is shortened from the left
    (``...tail``). ``local_commit`` is set only when ``item.path`` is committed and clean.
    """
    if len(message) > MAX_MESSAGE_CHARS:
        raise ValueError(f"The message is too long ({len(message)} characters; "
                         f"the limit is {MAX_MESSAGE_CHARS}).")
    if author is not None and len(author) > MAX_AUTHOR_CHARS:
        raise ValueError(f"The author is too long ({len(author)} characters; "
                         f"the limit is {MAX_AUTHOR_CHARS}).")
    source = {"machine": socket.gethostname(), "path": str(item.path)}
    commit = GitTracker.get_instance().head_commit(item.path)
    if commit:
        source["local_commit"] = commit
    manifest: dict[str, Any] = {
        "type": item.type,
        "name": item.name,
        "file_count": bundle.file_count,
        "uncompressed_size": bundle.uncompressed_size,
        "sha256": bundle.sha256,
        "message": message,
        "agent": agent,
        "source": _fit_source(source),
    }
    if author is not None:
        manifest["author"] = author
    return manifest
