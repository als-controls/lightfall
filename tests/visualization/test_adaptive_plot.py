"""Tests for AdaptivePlotVisualization."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Fake Tiled helpers (same pattern as test_adaptive_heatmap)
# ---------------------------------------------------------------------------


class FakeArray:
    def __init__(self, data: np.ndarray):
        self._data = data

    def read(self):
        return self._data.copy()


class FakeContainer:
    def __init__(
        self,
        children: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self._children = children or {}
        self._metadata = metadata or {}

    def __contains__(self, key: str) -> bool:
        return key in self._children

    def __getitem__(self, key: str):
        return self._children[key]

    def keys(self):
        return list(self._children.keys())

    @property
    def metadata(self):
        return self._metadata


def _make_iter(*, has_hp: bool = True, hp_vals: np.ndarray | None = None) -> FakeContainer:
    children: dict[str, Any] = {}
    if has_hp:
        hp = hp_vals if hp_vals is not None else np.array([1.0, 2.0, 3.0])
        children["hyperparameters"] = FakeArray(hp)
    # Add a dummy posterior_mean so iteration is valid
    children["posterior_mean"] = FakeArray(np.zeros((5, 5)))
    return FakeContainer(children=children)


def _make_adaptive(n_iters: int = 3, *, hp_per_iter: list[np.ndarray] | None = None, has_hp: bool = True) -> FakeContainer:
    config = FakeContainer(children={
        "evaluation_grid_x": FakeArray(np.linspace(0, 1, 5)),
        "evaluation_grid_y": FakeArray(np.linspace(0, 1, 5)),
    })
    children: dict[str, Any] = {"config": config}
    for i in range(1, n_iters + 1):
        hp_vals = hp_per_iter[i - 1] if hp_per_iter else None
        children[f"iter_{i:03d}"] = _make_iter(has_hp=has_hp, hp_vals=hp_vals)
    return FakeContainer(
        children=children, metadata={"adaptive_engine": "tsuchinoko"}
    )


def _make_run(adaptive: FakeContainer | None = None) -> FakeContainer:
    children: dict[str, Any] = {}
    if adaptive is not None:
        children["adaptive"] = adaptive
    return FakeContainer(children=children, metadata={"start": {}})


# ===========================================================================
# Tests
# ===========================================================================


class TestCanHandle:
    def test_valid_run_with_hyperparameters(self):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        adaptive = _make_adaptive(n_iters=2)
        run = _make_run(adaptive)
        assert AdaptivePlotVisualization.can_handle(run) == 70

    def test_returns_zero_for_no_adaptive(self):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        run = _make_run(adaptive=None)
        assert AdaptivePlotVisualization.can_handle(run) == 0

    def test_returns_zero_for_wrong_engine(self):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        adaptive = FakeContainer(
            children={"config": FakeContainer()},
            metadata={"adaptive_engine": "other"},
        )
        run = _make_run(adaptive)
        assert AdaptivePlotVisualization.can_handle(run) == 0

    def test_returns_zero_when_no_hyperparameters(self):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        adaptive = _make_adaptive(n_iters=1, has_hp=False)
        run = _make_run(adaptive)
        assert AdaptivePlotVisualization.can_handle(run) == 0

    def test_handles_garbage_gracefully(self):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        assert AdaptivePlotVisualization.can_handle(None) == 0
        assert AdaptivePlotVisualization.can_handle("nope") == 0


class TestSetRunAndFields:
    def test_get_fields_returns_hyperparameters(self, qtbot):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        w = AdaptivePlotVisualization()
        qtbot.addWidget(w)

        run = _make_run(_make_adaptive(n_iters=2))
        w.set_run(run)
        w.set_stream("adaptive")

        assert w.get_fields() == ["hyperparameters"]

    def test_set_field_updates_state(self, qtbot):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        w = AdaptivePlotVisualization()
        qtbot.addWidget(w)

        run = _make_run(_make_adaptive(n_iters=2))
        w.set_run(run)
        w.set_stream("adaptive")

        w.set_field("hyperparameters")
        assert w._field_name == "hyperparameters"


class TestPolling:
    def test_new_iteration_updates_plot(self, qtbot):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        w = AdaptivePlotVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        # Verify initial state
        assert len(w._iters) == 2

        # Add new iteration
        adaptive._children["iter_003"] = _make_iter()
        w._poll_tick()

        assert len(w._iters) == 3

    def test_stale_polling_stops_timer(self, qtbot):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        w = AdaptivePlotVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        w._poll_tick()
        w._poll_tick()
        assert w._poll_timer.isActive()
        w._poll_tick()
        assert not w._poll_timer.isActive()

    def test_new_data_resets_stale_count(self, qtbot):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        w = AdaptivePlotVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        w._poll_tick()
        w._poll_tick()
        assert w._stale_count == 2

        adaptive._children["iter_003"] = _make_iter()
        w._poll_tick()
        assert w._stale_count == 0


class TestPlotContent:
    """Verify that the plot actually creates lines for hyperparameter components."""

    def test_lines_created_per_component(self, qtbot):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        w = AdaptivePlotVisualization()
        qtbot.addWidget(w)

        hp_data = [
            np.array([1.0, 2.0, 3.0]),
            np.array([1.5, 2.5, 3.5]),
        ]
        adaptive = _make_adaptive(n_iters=2, hp_per_iter=hp_data)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        # Should have 3 lines (one per HP component)
        assert len(w._lines) == 3


class TestGetStreams:
    def test_returns_adaptive(self, qtbot):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        w = AdaptivePlotVisualization()
        qtbot.addWidget(w)

        run = _make_run(_make_adaptive(n_iters=1))
        w.set_run(run)

        assert w.get_streams() == ["adaptive"]


class TestClassAttributes:
    def test_viz_name(self):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        assert AdaptivePlotVisualization.viz_name == "adaptive_plot"

    def test_viz_display_name(self):
        from lucid.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        assert AdaptivePlotVisualization.viz_display_name == "Adaptive Plot"
