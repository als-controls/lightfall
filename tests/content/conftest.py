"""Shared helpers for content tests: zips in tmp dirs and ContentItems that describe them."""
from __future__ import annotations

import hashlib
import io
import zipfile

from lightfall.scopes.client import ContentItem


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def item_for(type_: str, name: str, revision: int, data: bytes, files: dict[str, bytes],
             **over) -> ContentItem:
    fields = {"type": type_, "name": name, "revision": revision,
              "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
              "file_count": len(files), "uncompressed_size": sum(map(len, files.values()))}
    fields.update(over)
    return ContentItem(**fields)
