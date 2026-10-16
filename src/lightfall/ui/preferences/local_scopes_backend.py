"""Serves facility/beamline/branch values from local scope files while server scopes are off.

Owns only keys the files mention. The user's YAML value overrides file values, except
where a file locks the key.
"""
from __future__ import annotations

from typing import Any

from lightfall_settings import SpecRegistry

from lightfall.scopes.keys import client_registry, is_local_only
from lightfall.scopes.state import FetchedState
from lightfall.scopes.store import MISSING, ScopedStore
from lightfall.ui.preferences.backend import PreferenceBackend


class LocalScopesBackend(PreferenceBackend):
    def __init__(self, local: Any, state: FetchedState,
                 registry: SpecRegistry | None = None) -> None:
        super().__init__()
        self._local = local
        self._store = ScopedStore(registry or client_registry(), state)
        self._keys = frozenset(k for k in self._store.keys() if not is_local_only(k))

    @property
    def chain(self) -> tuple[str, ...]:
        return self._store.chain

    def keys(self) -> set[str]:
        return set(self._keys)

    def owns(self, key: str) -> bool:
        return key in self._keys

    def get(self, key: str, default: Any = None) -> Any:
        res = self._store.resolve(key)
        if res.locked_at is not None:
            return res.value
        value = self._local.get(key, MISSING)
        if value is not MISSING:
            return value
        return res.value if res.origin is not None else default

    def set(self, key: str, value: Any) -> None:
        self._local.store_quiet(key, value)
        self.changed.emit(key, self.get(key))

    def remove(self, key: str) -> None:
        self._local.store_quiet(key, None)
        self.changed.emit(key, self.get(key))
