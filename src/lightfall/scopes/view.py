"""Read model for the Scopes page: chain, roles, keys and where each value comes from."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lightfall_settings import (
    Lock,
    ScopeId,
    ScopeSnapshot,
    deployment_registry,
    resolve,
    spec_from_dict,
    user_only_spec,
)
from lightfall_settings.scopes import KIND_RANK

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.client import ScopesClient, ScopesError, Snapshot, TreeEntry
from lightfall.scopes.state import fetch_state
from lightfall.utils.logging import logger


@dataclass(frozen=True)
class KeyRow:
    key: str
    status: str
    scopes: tuple[str, ...]
    value: Any
    origin: str | None
    locked_at: str | None
    lock_reason: str
    rejected_at: tuple[str, ...]


@dataclass(frozen=True)
class ScopesView:
    bind: str
    chain: tuple[str, ...]
    roles: dict[str, str | None]
    entitled: tuple[str, ...]
    facility_label: str
    keys: tuple[KeyRow, ...]
    keys_restricted: bool
    offline: bool
    cached_at: float | None
    server: str = ""
    tree: tuple[TreeEntry, ...] = ()  # every beamline/branch, with the caller's role


def _kinds(spec: Any) -> tuple[str, ...]:
    return tuple(sorted(spec.scopes, key=KIND_RANK.__getitem__))


def _spec(record: dict[str, Any], field: str) -> Any | None:
    raw = record.get(field)
    if not isinstance(raw, dict):
        return None
    try:
        return spec_from_dict({**raw, "key": record["key"]})
    except ValueError as e:
        logger.warning("Ignoring malformed spec for {}: {}", record.get("key"), e)
        return None


def _to_resolver(snap: Snapshot) -> ScopeSnapshot:
    locks = {}
    for key, raw in snap.locks.items():
        if isinstance(raw, dict):
            locks[key] = Lock(locked_by=str(raw.get("locked_by", "")),
                              reason=str(raw.get("reason", "") or ""),
                              locked_at=str(raw.get("locked_at", "") or ""))
    return ScopeSnapshot(scope=ScopeId.parse(snap.scope), settings=dict(snap.settings),
                         locks=locks)


def _resolved_row(spec: Any, status: str, snaps: list[ScopeSnapshot]) -> KeyRow:
    allowed = [s for s in snaps if s.scope.kind in spec.scopes]
    res = resolve(spec, allowed)
    return KeyRow(
        key=spec.key, status=status, scopes=_kinds(spec), value=res.value,
        origin=str(res.origin) if res.origin else None,
        locked_at=str(res.locked_at) if res.locked_at else None,
        lock_reason=res.lock.reason if res.lock else "",
        rejected_at=tuple(str(s) for s in res.rejected),
    )


def _server(client: ScopesClient) -> str:
    try:
        return str(getattr(client, "base_url", "") or "")
    except Exception:
        return ""


def load_view(client: ScopesClient, cache: SnapshotCache, bind: str,
              identity: str | None = None) -> ScopesView:
    """``identity`` is the logged-in username (None when logged out); it keys the
    per-user parts of the offline cache."""
    state = fetch_state(client, cache, bind, identity)
    info, offline = state.chain, state.offline
    snaps = [_to_resolver(state.snapshots[scope]) for scope in info.chain]

    records: list[dict[str, Any]] | None
    if offline:
        records = (cache.load_keys(identity) if identity else None) or []
    else:
        try:
            records = client.setting_keys()
        except ScopesError as e:
            logger.warning("Setting keys unavailable: {}", e)
            records = (cache.load_keys(identity) if identity else None) or []
        else:
            if records is not None and identity is not None:
                cache.save_keys(records, identity)

    registry = deployment_registry()  # the specs the server enforces for every key
    rows: dict[str, KeyRow] = {}
    for key in registry.keys():
        rows[key] = _resolved_row(registry.get(key), "core", snaps)
    for rec in records or []:
        key = rec["key"]
        if key in rows:
            continue
        status = str(rec.get("status", ""))
        if status == "approved":
            spec = _spec(rec, "spec")
            if spec is not None:
                rows[key] = _resolved_row(spec, "approved", snaps)
            continue
        spec = _spec(rec, "proposed_spec")
        rows[key] = KeyRow(key=key, status=status, scopes=_kinds(spec) if spec else (),
                           value=None, origin=None, locked_at=None, lock_reason="",
                           rejected_at=())
    for snap in snaps:
        for key in snap.settings:
            if key not in rows:
                rows[key] = _resolved_row(user_only_spec(key), "undeclared", snaps)

    return ScopesView(
        bind=bind, chain=info.chain, roles=dict(info.roles), entitled=info.entitled,
        facility_label=info.facility_label,
        keys=tuple(rows[k] for k in sorted(rows)),
        keys_restricted=records is None,
        offline=offline, cached_at=state.cached_at,
        server=_server(client),
        tree=info.tree,
    )
