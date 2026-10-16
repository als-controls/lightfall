"""In-memory snapshots for one scope chain, with resolution and optimistic edits.

GUI-thread only; no I/O. ScopedBackend owns one of these while scopes are active.
"""
from __future__ import annotations

from typing import Any

from lightfall_settings import Lock, Resolution, ScopeId, ScopeSnapshot, SpecRegistry, resolve

from lightfall.scopes.state import FetchedState

MISSING: Any = object()


class ScopedStore:
    def __init__(self, registry: SpecRegistry, state: FetchedState) -> None:
        self._registry = registry
        self.replace(state)

    def replace(self, state: FetchedState) -> None:
        self.chain: tuple[str, ...] = tuple(state.chain.chain)
        self.offline = state.offline
        self._settings: dict[str, dict[str, Any]] = {}
        self._versions: dict[str, dict[str, int]] = {}
        self._locks: dict[str, dict[str, Lock]] = {}
        for scope in self.chain:
            snap = state.snapshots.get(scope)
            self._settings[scope] = dict(snap.settings) if snap else {}
            self._versions[scope] = dict(snap.versions) if snap else {}
            self._locks[scope] = {
                k: Lock(locked_by=str(v.get("locked_by", "")), reason=str(v.get("reason") or ""),
                        locked_at=str(v.get("locked_at") or ""))
                for k, v in (snap.locks.items() if snap else ()) if isinstance(v, dict)
            }

    @property
    def user_scope(self) -> str | None:
        last = self.chain[-1] if self.chain else None
        return last if last is not None and last.startswith("user:") else None

    def keys(self) -> set[str]:
        return {k for settings in self._settings.values() for k in settings}

    def user_settings(self) -> dict[str, Any]:
        return dict(self._settings.get(self.user_scope or "", {}))

    def resolve(self, key: str) -> Resolution:
        spec = self._registry.get(key)
        snaps = []
        for scope in self.chain:
            sid = ScopeId.parse(scope)
            if sid.kind in spec.scopes:
                snaps.append(ScopeSnapshot(scope=sid, settings=self._settings[scope],
                                           locks=self._locks[scope]))
        return resolve(spec, snaps)

    def value(self, key: str) -> Any:
        res = self.resolve(key)
        return MISSING if res.origin is None else res.value

    def user_value(self, key: str) -> Any:
        scope = self.user_scope
        return MISSING if scope is None else self._settings[scope].get(key, MISSING)

    def version(self, scope: str, key: str) -> int | None:
        return self._versions.get(scope, {}).get(key)

    def set_version(self, scope: str, key: str, version: int) -> None:
        if scope not in self._versions:
            return
        if version:
            self._versions[scope][key] = version
        else:
            self._versions[scope].pop(key, None)

    def put_local(self, scope: str, key: str, value: Any) -> None:
        if scope in self._settings:
            self._settings[scope][key] = value

    def drop_local(self, scope: str, key: str) -> None:
        if scope in self._settings:
            self._settings[scope].pop(key, None)
            self._versions[scope].pop(key, None)

    def restore(self, scope: str, key: str, value: Any, version: int | None) -> None:
        if value is MISSING:
            self.drop_local(scope, key)
            return
        self.put_local(scope, key, value)
        if version:
            self.set_version(scope, key, version)
