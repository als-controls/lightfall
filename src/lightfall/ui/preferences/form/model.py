"""Per-scope edit buffers for the scope-aware preferences form (Qt-free).

Only fields the user actually edited (and that differ from what was loaded)
produce write operations, so untouched settings are never written.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_Cell = tuple[str, str]  # (scope, key)


@dataclass(frozen=True)
class WriteOp:
    """One write to issue: set ``key`` at ``scope`` (value None removes it)."""

    scope: str
    key: str
    value: Any


def _same(a: Any, b: Any) -> bool:
    """Equality that keeps None, False, 0 and "" distinct from one another."""
    if a is None or b is None:
        return a is None and b is None
    return type(a) is type(b) and a == b


class _ScopeValues:
    """FormValues implementation: pending values at one scope."""

    def __init__(self, model: FormModel, scope: str) -> None:
        self._model = model
        self.scope = scope

    def get(self, key: str) -> Any:
        return self._model.pending(self.scope, key)


class FormModel:
    """Loaded values, pending edits and conflicts, kept per (scope, key)."""

    def __init__(self, read_layer: Callable[[str, str], Any]) -> None:
        self._read = read_layer
        self._loaded: dict[_Cell, Any] = {}
        self._edits: dict[_Cell, Any] = {}
        self._conflicts: set[_Cell] = set()

    def loaded(self, scope: str, key: str) -> Any:
        cell = (scope, key)
        if cell not in self._loaded:
            self._loaded[cell] = self._read(key, scope)
        return self._loaded[cell]

    def pending(self, scope: str, key: str) -> Any:
        cell = (scope, key)
        if cell in self._edits:
            return self._edits[cell]
        return self.loaded(scope, key)

    def edit(self, scope: str, key: str, value: Any) -> None:
        cell = (scope, key)
        loaded = self.loaded(scope, key)
        self._edits[cell] = value
        if _same(value, loaded):
            self._conflicts.discard(cell)

    def is_dirty(self, scope: str, key: str) -> bool:
        cell = (scope, key)
        return cell in self._edits and not _same(self._edits[cell], self.loaded(scope, key))

    def dirty_scopes(self) -> set[str]:
        return {scope for scope, key in self._edits if self.is_dirty(scope, key)}

    def reload(self, scope: str, key: str) -> bool:
        cell = (scope, key)
        fresh = self._read(key, scope)
        moved = cell in self._loaded and not _same(self._loaded[cell], fresh)
        self._loaded[cell] = fresh
        if moved and cell in self._edits:
            self._conflicts.add(cell)
        return moved

    def conflict(self, scope: str, key: str) -> bool:
        return (scope, key) in self._conflicts and self.is_dirty(scope, key)

    def ops(self, writable: Callable[[str, str], bool]) -> list[WriteOp]:
        cells = sorted(c for c in self._edits if self.is_dirty(*c) and writable(c[1], c[0]))
        return [WriteOp(scope, key, self._edits[(scope, key)]) for scope, key in cells]

    def commit(self, scope: str, key: str) -> None:
        cell = (scope, key)
        if cell in self._edits:
            self._loaded[cell] = self._edits.pop(cell)
        self._conflicts.discard(cell)

    def discard(self) -> None:
        self._edits.clear()
        self._conflicts.clear()

    def values(self, scope: str) -> _ScopeValues:
        return _ScopeValues(self, scope)
