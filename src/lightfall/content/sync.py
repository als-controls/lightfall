"""Bring this machine's copy of a scope's content in line with the server (no Qt here)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lightfall.content.extract import UnsafeBundle
from lightfall.content.store import ContentStore, _is_shared_scope, valid_item
from lightfall.scopes.client import ContentItem, ContentListing, ScopesError
from lightfall.utils.logging import logger


@dataclass(frozen=True)
class ScopeSyncResult:
    scope: str
    changed: tuple[tuple[str, str], ...] = ()  # (type, name) installed or updated
    removed: tuple[tuple[str, str], ...] = ()  # (type, name) retracted
    failed: tuple[tuple[str, str, str], ...] = ()  # (type, name, reason); ("", "", …) = listing
    unchanged: bool = False


@dataclass(frozen=True)
class SyncResult:
    bind: str
    scopes: tuple[ScopeSyncResult, ...]
    offline: bool

    @property
    def changed_types(self) -> frozenset[str]:
        return frozenset(t for s in self.scopes for t, _ in (*s.changed, *s.removed))


def _fetch_item(client: Any, store: ContentStore, scope: str, item: ContentItem) -> None:
    """Download, verify and install one item; raises on any failure."""
    tmp = store.new_staging_file()
    try:
        sha = client.download_bundle(scope, item.type, item.name, item.revision, tmp,
                                     expected_size=item.size)
        if sha != item.sha256:
            raise UnsafeBundle(f"sha256 {sha[:12]}… doesn't match the listed {item.sha256[:12]}…")
        store.install(scope, item, tmp)
    finally:
        tmp.unlink(missing_ok=True)


def sync_scope(client: Any, store: ContentStore, scope: str) -> ScopeSyncResult:
    """Install new and updated items, remove retracted ones. A failed item keeps its
    previous copy and stays at its old revision in the index, so the next sync retries it.

    Raises ScopesError when the listing itself can't be fetched.
    """
    local = store.listing(scope)
    fresh = client.content_listing(scope, etag=local.etag if local and local.etag else None)
    if fresh is None:
        return ScopeSyncResult(scope, unchanged=True)
    have = {(i.type, i.name): i for i in local.items} if local else {}
    keep: list[ContentItem] = []
    changed: list[tuple[str, str]] = []
    failed: list[tuple[str, str, str]] = []
    wanted: set[tuple[str, str]] = set()
    for item in fresh.items:
        key = (item.type, item.name)
        if not valid_item(*key):
            logger.warning("Skipping invalid content item {}/{!r} at {}", item.type, item.name,
                           scope)
            continue
        wanted.add(key)
        old = have.get(key)
        if old is not None and (old.revision, old.sha256) == (item.revision, item.sha256):
            keep.append(item)
            continue
        try:
            _fetch_item(client, store, scope, item)
        except (ScopesError, UnsafeBundle, OSError) as e:
            logger.warning("Could not sync {} {} at {}: {}", item.type, item.name, scope, e)
            failed.append((item.type, item.name, str(e)))
            if old is not None:
                keep.append(old)
            continue
        keep.append(item)
        changed.append(key)
    removed: list[tuple[str, str]] = []
    for key in have:
        if key in wanted:
            continue
        try:
            store.remove(scope, *key)
        except (OSError, ValueError) as e:
            logger.warning("Could not remove retracted {} {} at {}: {}", *key, scope, e)
            failed.append((*key, str(e)))
            keep.append(have[key])
            continue
        removed.append(key)
    store.save_listing(ContentListing(scope, tuple(keep), "" if failed else fresh.etag))
    return ScopeSyncResult(scope, tuple(changed), tuple(removed), tuple(failed))


def sync_chain(client: Any, store: ContentStore, bind: str, chain: Sequence[str]) -> SyncResult:
    """Sync every shared scope of ``chain`` and record it as ``bind``'s chain."""
    scopes = [s for s in chain if _is_shared_scope(s)]
    store.save_chain(bind, scopes)
    results: list[ScopeSyncResult] = []
    for scope in scopes:
        try:
            results.append(sync_scope(client, store, scope))
        except ScopesError as e:
            if e.status is None:
                logger.info("Content sync offline: {}", e)
                return SyncResult(bind, tuple(results), offline=True)
            logger.warning("Content for {} unavailable: {}", scope, e)
            results.append(ScopeSyncResult(scope, failed=(("", "", str(e)),)))
    return SyncResult(bind, tuple(results), offline=False)
