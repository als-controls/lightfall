"""Per-scope edit buffers for the scope-aware preferences form (Qt-free).

Only fields the user actually edited (and that differ from what was loaded)
produce write operations, so untouched settings are never written.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
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

    def effective(self, key: str, default: Any = None) -> Any:
        return self._model.effective(self.scope, key, default)


class _FrozenValues:
    """FormValues copied at one moment, safe to read from a worker thread.

    Holds the given keys only; any other key reads as not set (``default``).
    """

    def __init__(self, scope: str, pending: dict[str, Any], effective: dict[str, Any]) -> None:
        self.scope = scope
        self._pending = pending
        self._effective = effective

    def get(self, key: str) -> Any:
        return self._pending.get(key)

    def effective(self, key: str, default: Any = None) -> Any:
        value = self._effective.get(key)
        return default if value is None else value


class FormModel:
    """Loaded values, pending edits and conflicts, kept per (scope, key).

    ``resolve(key, default)`` (the dialog passes ``prefs.get``) gives the value
    currently in effect across all scopes; it backs ``effective``.
    """

    def __init__(
        self,
        read_layer: Callable[[str, str], Any],
        resolve: Callable[[str, Any], Any] | None = None,
    ) -> None:
        self._read = read_layer
        self._resolve = resolve
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

    def effective(self, scope: str, key: str, default: Any = None) -> Any:
        """The value a predicate or action should act on for ``key`` at ``scope``.

        The user's edit at ``scope`` if they made one and it is not None;
        otherwise the currently resolved value ``resolve(key, default)``.
        Without a resolver: the pending value at ``scope``, or ``default``.

        Approximation: after a Clear at ``scope`` this returns the currently
        resolved value (which may still be the cleared one, until saved), not
        what the lower layers alone would give.
        """
        edit = self._edits.get((scope, key))
        if edit is not None:
            return edit
        if self._resolve is not None:
            return self._resolve(key, default)
        value = self.pending(scope, key)
        return default if value is None else value

    def frozen_values(self, scope: str, keys: Iterable[str]) -> _FrozenValues:
        """A snapshot of ``get`` and ``effective`` for ``keys`` at ``scope``."""
        keys = list(keys)
        return _FrozenValues(
            scope,
            {k: self.pending(scope, k) for k in keys},
            {k: self.effective(scope, k) for k in keys},
        )

    def edit(self, scope: str, key: str, value: Any) -> None:
        cell = (scope, key)
        loaded = self.loaded(scope, key)
        self._edits[cell] = value
        if _same(value, loaded):
            self._conflicts.discard(cell)

    def is_dirty(self, scope: str, key: str) -> bool:
        cell = (scope, key)
        return cell in self._edits and not _same(self._edits[cell], self.loaded(scope, key))

    def dirty_cells(self) -> list[tuple[str, str]]:
        """Every (scope, key) with an unsaved edit, sorted."""
        return sorted(c for c in self._edits if self.is_dirty(*c))

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

    def discard_cell(self, scope: str, key: str) -> None:
        """Drop the edit (and conflict flag) of one cell; other cells keep theirs."""
        cell = (scope, key)
        self._edits.pop(cell, None)
        self._conflicts.discard(cell)

    def discard(self) -> None:
        self._edits.clear()
        self._conflicts.clear()

    def values(self, scope: str) -> _ScopeValues:
        return _ScopeValues(self, scope)
