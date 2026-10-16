"""Fetch a scope chain and its snapshots, from the server or the disk cache."""
from __future__ import annotations

from dataclasses import dataclass

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.client import ChainInfo, ScopesClient, ScopesError, Snapshot
from lightfall.utils.logging import logger


def _fetch_chain(client: ScopesClient, cache: SnapshotCache, bind: str,
                 identity: str | None) -> tuple[ChainInfo, float | None]:
    try:
        info = client.chain(bind)
    except ScopesError as e:
        if e.status is not None:
            raise
        if identity is None:  # nobody logged in: never show someone's cached chain
            raise ScopesError("Log in to see scoped settings", status=401) from e
        cached = cache.load_chain(bind, identity)
        if cached is None:
            raise
        return cached
    if identity is not None:
        cache.save_chain(bind, info, identity)
    return info, None


def _fetch_snapshot(client: ScopesClient, cache: SnapshotCache, scope: str, offline: bool
                    ) -> tuple[Snapshot, float | None, bool]:
    """Returns (snapshot, cached_at if served from cache, offline)."""
    cached = cache.load_snapshot(scope)
    if not offline:
        try:
            fresh = client.snapshot(scope, etag=cached[0].etag if cached else None)
        except ScopesError as e:
            if e.status is not None:
                logger.warning("Snapshot {} unavailable: {}", scope, e)
                return Snapshot.empty(scope), None, False
            offline = True
        else:
            if fresh is not None:
                cache.save_snapshot(fresh)
                return fresh, None, False
            if cached is not None:  # 304
                return cached[0], None, False
            return Snapshot.empty(scope), None, False
    if cached is not None:
        return cached[0], cached[1], True
    return Snapshot.empty(scope), None, True


@dataclass(frozen=True)
class FetchedState:
    chain: ChainInfo
    snapshots: dict[str, Snapshot]
    offline: bool
    cached_at: float | None


def fetch_state(client: ScopesClient, cache: SnapshotCache, bind: str,
                identity: str | None) -> FetchedState:
    info, chain_cached_at = _fetch_chain(client, cache, bind, identity)
    offline = chain_cached_at is not None
    cached_times = [chain_cached_at] if chain_cached_at else []
    snapshots: dict[str, Snapshot] = {}
    for scope in info.chain:
        snap, cached_at, offline = _fetch_snapshot(client, cache, scope, offline)
        if cached_at is not None:
            cached_times.append(cached_at)
        snapshots[scope] = snap
    return FetchedState(chain=info, snapshots=snapshots, offline=offline,
                        cached_at=min(cached_times) if cached_times else None)
