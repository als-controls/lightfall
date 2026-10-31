"""Bring this machine's copy of a scope's content in line with the server (no Qt here)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from filelock import FileLock, Timeout

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
    """Install new and updated items, drop retracted ones from the index. A failed item keeps its
    previous copy and stays at its old revision in the index, so the next sync retries it.

    Raises ScopesError when the listing itself can't be fetched.
    """
    local = store.listing(scope)
    have = {(i.type, i.name): i for i in local.items} if local else {}
    present = {key for key, i in have.items()
               if valid_item(*key) and store.item_path(scope, *key, i.revision).is_dir()}
    # A folder that went missing must be repaired, so don't let the server say "304".
    etag = local.etag if local and local.etag and len(present) == len(have) else None
    fresh = client.content_listing(scope, etag=etag)
    if fresh is None:
        return ScopeSyncResult(scope, unchanged=True)
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
        if (old is not None and key in present
                and (old.revision, old.sha256) == (item.revision, item.sha256)):
            keep.append(item)
            continue
        try:
            _fetch_item(client, store, scope, item)
        except Exception as e:  # one bad item must not stop the rest of the sync
            logger.warning("Could not sync {} {} at {}: {}", item.type, item.name, scope, e)
            failed.append((item.type, item.name, str(e)))
            if old is not None and key in present:
                keep.append(old)
            continue
        keep.append(item)
        changed.append(key)
    # Retracted: dropped from the index; the folder goes at the next prune (it may be loaded).
    removed = [key for key in have if key not in wanted]
    store.save_listing(ContentListing(scope, tuple(keep), "" if failed else fresh.etag))
    return ScopeSyncResult(scope, tuple(changed), tuple(removed), tuple(failed))


def sync_chain(client: Any, store: ContentStore, bind: str, chain: Sequence[str], *,
               prune: bool = False) -> SyncResult:
    """Sync every shared scope of ``chain`` and record it as ``bind``'s chain.

    ``prune`` first removes folders no index names (a process's first sync). Skipped,
    with an empty result, while another process on this machine is syncing.
    """
    store.root.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(store.lock_path), timeout=0):
            if prune:
                store.prune()
            return _sync_chain(client, store, bind, chain)
    except Timeout:
        logger.info("Another Lightfall on this machine is syncing content; skipped")
        return SyncResult(bind, (), offline=False)


def _sync_chain(client: Any, store: ContentStore, bind: str, chain: Sequence[str]) -> SyncResult:
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
