"""Tests for the progressive reduction engine."""
from __future__ import annotations

import numpy as np

from lightfall.visualization.reductions import REDUCTIONS_BY_NAME
from lightfall.visualization.reduction_engine import (
    ReductionEngine,
    iter_point_values,
)


def _cubes():
    # 3 scan points, each a (2, 2, 2) sub-cube
    return [
        np.full((2, 2, 2), float(p), dtype=np.float64) for p in range(3)
    ]


def test_iter_point_values_yields_scalar_per_point():
    cubes = _cubes()
    op = REDUCTIONS_BY_NAME["Mean"]
    results = list(iter_point_values(3, lambda p: cubes[p], op))
    assert [p for p, _ in results] == [0, 1, 2]
    assert [v for _, v in results] == [0.0, 1.0, 2.0]


def test_iter_point_values_emits_nan_on_fetch_error():
    op = REDUCTIONS_BY_NAME["Mean"]

    def bad_fetch(p):
        if p == 1:
            raise RuntimeError("boom")
        return np.ones((2, 2, 2), dtype=np.float64)

    results = dict(iter_point_values(3, bad_fetch, op))
    assert results[0] == 1.0
    assert np.isnan(results[1])
    assert results[2] == 1.0


def test_engine_emits_points_then_finished(qtbot):
    cubes = _cubes()
    op = REDUCTIONS_BY_NAME["Mean"]
    engine = ReductionEngine()

    received: list[tuple[int, float]] = []
    engine.pointComputed.connect(lambda p, v: received.append((p, v)))

    with qtbot.waitSignal(engine.finished, timeout=5000):
        engine.start(3, lambda p: cubes[p], op)

    assert sorted(received) == [(0, 0.0), (1, 1.0), (2, 2.0)]
