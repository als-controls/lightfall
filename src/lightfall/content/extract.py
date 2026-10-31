"""Unpack a content bundle without trusting it (the server never opens bundles)."""
from __future__ import annotations

import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

_DRIVE = re.compile(r"^[A-Za-z]:")
_CHUNK = 64 * 1024


class UnsafeBundle(Exception):
    """Not a zip, or a member could escape its folder or exceeds what was declared."""


def _member_parts(name: str) -> tuple[str, ...]:
    norm = name.replace("\\", "/")
    if not norm or norm.startswith("/") or _DRIVE.match(norm):
        raise UnsafeBundle(f"absolute path in bundle: {name!r}")
    parts = tuple(p for p in PurePosixPath(norm).parts if p not in ("", "."))
    if not parts:
        raise UnsafeBundle(f"empty path in bundle: {name!r}")
    if ".." in parts:
        raise UnsafeBundle(f"'..' in bundle path: {name!r}")
    if any(":" in p for p in parts):  # NTFS alternate data streams
        raise UnsafeBundle(f"':' in bundle path: {name!r}")
    return parts


def safe_extract(zip_path: Path, dest: Path, *, max_files: int, max_bytes: int) -> int:
    """Extract ``zip_path`` into the existing empty folder ``dest``; returns the file count.

    Raises UnsafeBundle; ``dest`` may then hold a partial extraction and must be discarded.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except Exception as e:
        raise UnsafeBundle(f"not a zip: {e}") from e
    with zf:
        seen: set[str] = set()
        files: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        for info in zf.infolist():
            if stat.S_ISLNK(info.external_attr >> 16):
                raise UnsafeBundle(f"symlink in bundle: {info.filename!r}")
            parts = _member_parts(info.filename)
            if info.is_dir():
                continue
            key = "/".join(parts).casefold()  # Windows: A.py and a.py are one file
            if key in seen:
                raise UnsafeBundle(f"duplicate path in bundle: {info.filename!r}")
            seen.add(key)
            files.append((info, parts))
        if len(files) > max_files:
            raise UnsafeBundle(f"{len(files)} files; declared at most {max_files}")
        total = 0  # counted while copying: the headers' sizes are the bundle's word
        try:
            for info, parts in files:
                target = dest.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    while chunk := src.read(_CHUNK):
                        total += len(chunk)
                        if total > max_bytes:
                            raise UnsafeBundle(f"more than the declared {max_bytes} bytes")
                        out.write(chunk)
        except UnsafeBundle:
            raise
        except Exception as e:  # corrupt data (zlib.error), deflate64, encryption, ...
            raise UnsafeBundle(f"unreadable bundle: {type(e).__name__}: {e}") from e
    return len(files)
