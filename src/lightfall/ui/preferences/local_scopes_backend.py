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
        self._registry = registry or client_registry()
        self._store = ScopedStore(self._registry, state)
        self._keys = frozenset(k for k in self._store.keys() if not is_local_only(k))

    @property
    def chain(self) -> tuple[str, ...]:
        return self._store.chain

    def resolution(self, key: str) -> Any:
        """How ``key`` resolves through the scope files (origin, lock)."""
        return self._store.resolve(key)

    def layer_value(self, key: str, scope: str) -> Any:
        """One layer's own value (MISSING when unset there).

        "local" is this console's YAML, where ``set``/``remove`` write; any other
        scope is that scope file's value.
        """
        if scope == "local":
            value = self._local.get(key, MISSING)
            return MISSING if value is None else value
        return self._store.scope_value(scope, key)

    def item_origins(self, key: str) -> dict[str, str]:
        """For an item-merged key: item id -> the scope it comes from (``"local"`` = this
        console's YAML, which overrides the files item by item)."""
        if self._registry.get(key).policy != "union":
            return {}
        res = self._store.resolve(key)
        origins = {name: str(scope) for name, scope in res.item_origins.items()}
        origins.update(dict.fromkeys(self._console_items(key) or {}, "local"))
        return origins

    def _console_items(self, key: str) -> dict[str, Any] | None:
        """The console YAML items that apply to an item-merged key, or None when the
        console holds no map for it or a file lock suppresses it. ``{}`` is a map: it
        adds nothing but keeps the file items (never the scalar path). One helper for
        ``get`` and ``item_origins`` so they cannot diverge.

        In scope-file mode the console YAML is the only writable layer, so its items
        override file items per item (as scalars do); in server mode the YAML is only
        a fallback.
        """
        value = self._local.get(key, None)
        if not isinstance(value, dict) or self._store.resolve(key).locked_at is not None:
            return None
        return {k: v for k, v in value.items() if v is not None}

    def keys(self) -> set[str]:
        return set(self._keys)

    def owns(self, key: str) -> bool:
        return key in self._keys

    def get(self, key: str, default: Any = None) -> Any:
        res = self._store.resolve(key)
        if res.locked_at is not None:
            return res.value
        if self._registry.get(key).policy == "union":
            local = self._console_items(key)
            if local is not None:
                return {**(res.value if res.origin is not None else {}), **local}
        value = self._local.get(key, MISSING)
        if value is not MISSING and value is not None:  # remove() stores None
            return value
        return res.value if res.origin is not None else default

    def set(self, key: str, value: Any) -> None:
        self._local.store_quiet(key, value)
        self.changed.emit(key, self.get(key))

    def remove(self, key: str) -> None:
        self._local.store_quiet(key, None)
        self.changed.emit(key, self.get(key))
