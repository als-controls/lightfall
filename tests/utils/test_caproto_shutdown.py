"""Tests for caproto shutdown helpers.

The drain path must stop each circuit's user-callback ThreadPoolExecutor
(so its non-daemon workers don't stall interpreter exit) without touching
sockets, and must be robust to partially-formed / absent contexts.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from lightfall.utils.caproto_shutdown import drain_callback_executors


def test_drain_shuts_down_all_circuit_executors() -> None:
    ex1 = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cb1")
    ex2 = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cb2")
    ctx = SimpleNamespace(
        circuit_managers={
            ("h1", 0): SimpleNamespace(user_callback_executor=ex1),
            ("h2", 0): SimpleNamespace(user_callback_executor=ex2),
        }
    )

    drained = drain_callback_executors(ctx)

    assert drained == 2
    # After shutdown, submitting new work is rejected (real behavior, no mocks).
    for ex in (ex1, ex2):
        with pytest.raises(RuntimeError):
            ex.submit(lambda: None)


def test_drain_none_context_is_noop() -> None:
    assert drain_callback_executors(None) == 0


def test_drain_tolerates_missing_attributes() -> None:
    # Context with no circuit_managers at all.
    assert drain_callback_executors(SimpleNamespace()) == 0
    # A circuit manager that has no executor attribute.
    ctx = SimpleNamespace(circuit_managers={("h", 0): SimpleNamespace()})
    assert drain_callback_executors(ctx) == 0
