"""Tests for AdaptiveHeatmapVisualization."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Fake Tiled helpers (dict-backed stand-ins)
# ---------------------------------------------------------------------------


class FakeArray:
    """Fake Tiled array node: supports .read()."""

    def __init__(self, data: np.ndarray):
        self._data = data

    def read(self):
        return self._data.copy()


class FakeContainer:
    """Fake Tiled container node: supports [], .keys(), .metadata."""

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


def _make_iter_container(
    grid_res: int = 10,
    *,
    has_mean: bool = True,
    has_var: bool = True,
    has_acq: bool = True,
    has_hp: bool = True,
    has_targets: bool = True,
) -> FakeContainer:
    """Build a single iter_NNN container with optional arrays."""
    arrays: dict[str, Any] = {}
    if has_mean:
        arrays["posterior_mean"] = FakeArray(np.random.rand(grid_res, grid_res))
    if has_var:
        arrays["posterior_variance"] = FakeArray(np.random.rand(grid_res, grid_res))
    if has_acq:
        arrays["acquisition_function"] = FakeArray(np.random.rand(grid_res, grid_res))
    if has_hp:
        arrays["hyperparameters"] = FakeArray(np.array([1.0, 2.0, 3.0]))
    if has_targets:
        arrays["targets"] = FakeArray(np.array([[25.0, 12.5], [50.0, 25.0]]))
    return FakeContainer(children=arrays)


def _make_adaptive_container(
    n_iters: int = 3,
    grid_res: int = 10,
    *,
    has_z: bool = False,
    iter_kwargs: dict | None = None,
) -> FakeContainer:
    """Build a fake adaptive container with config + iter_NNN children."""
    config_children: dict[str, Any] = {
        "evaluation_grid_x": FakeArray(np.linspace(0, 100, grid_res)),
        "evaluation_grid_y": FakeArray(np.linspace(0, 50, grid_res)),
    }
    if has_z:
        config_children["evaluation_grid_z"] = FakeArray(np.linspace(0, 25, grid_res))
    config = FakeContainer(children=config_children)

    children: dict[str, Any] = {"config": config}
    kw = iter_kwargs or {}
    for i in range(1, n_iters + 1):
        children[f"iter_{i:03d}"] = _make_iter_container(grid_res, **kw)

    return FakeContainer(
        children=children, metadata={"adaptive_engine": "tsuchinoko"}
    )


def _make_run(
    adaptive: FakeContainer | None = None,
    *,
    primary_events: dict[str, np.ndarray] | None = None,
    dim_fields: tuple[str, str] = ("motor_x", "motor_y"),
) -> FakeContainer:
    """Build a top-level fake BlueskyRun with 'adaptive' and optional 'primary'."""
    children: dict[str, Any] = {}
    if adaptive is not None:
        children["adaptive"] = adaptive

    # Build a minimal primary stream for measurement overlay tests
    if primary_events is not None:
        events_data = {k: FakeArray(v) for k, v in primary_events.items()}
        events = FakeContainer(children=events_data)
        internal = FakeContainer(children={"events": events})
        primary = FakeContainer(children={"internal": internal})
        children["primary"] = primary

    start_md: dict[str, Any] = {}
    if dim_fields:
        dims = [([dim_fields[0]], "primary"), ([dim_fields[1]], "primary")]
        start_md = {"hints": {"dimensions": dims}}

    return FakeContainer(children=children, metadata={"start": start_md})


# ===========================================================================
# Tests
# ===========================================================================


class TestCanHandle:
    """can_handle scoring tests."""

    def test_valid_2d_tsuchinoko_run(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = _make_adaptive_container(n_iters=2)
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 90

    def test_returns_zero_for_no_adaptive(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        run = _make_run(adaptive=None)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_returns_zero_for_wrong_engine(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = FakeContainer(
            children={"config": FakeContainer()},
            metadata={"adaptive_engine": "other"},
        )
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_returns_zero_for_3d_grid(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = _make_adaptive_container(n_iters=1, has_z=True)
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_returns_zero_for_missing_metadata(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = FakeContainer(
            children={"config": FakeContainer()}, metadata={}
        )
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_returns_zero_for_no_config(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = FakeContainer(
            children={}, metadata={"adaptive_engine": "tsuchinoko"}
        )
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_handles_exception_gracefully(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        assert AdaptiveHeatmapVisualization.can_handle(None) == 0
        assert AdaptiveHeatmapVisualization.can_handle("garbage") == 0


class TestSetRunAndFields:
    """set_run + get_fields integration."""

    def test_get_fields_returns_present_arrays(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        fields = w.get_fields()
        assert "posterior_mean" in fields
        assert "posterior_variance" in fields
        assert "acquisition_function" in fields

    def test_get_fields_only_returns_present(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(
            n_iters=1, iter_kwargs={"has_acq": False}
        )
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        fields = w.get_fields()
        assert "acquisition_function" not in fields
        assert "posterior_mean" in fields

    def test_get_fields_preserves_priority_order(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=1)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        fields = w.get_fields()
        assert fields == [
            "posterior_mean",
            "posterior_variance",
            "acquisition_function",
        ]


class TestSetField:
    """set_field changes displayed data."""

    def test_switching_field_updates_internal_state(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=1)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        w.set_field("posterior_mean")
        assert w._field_name == "posterior_mean"

        w.set_field("posterior_variance")
        assert w._field_name == "posterior_variance"


class TestIterationDiscovery:
    """Iteration discovery and slider logic."""

    def test_discovers_sorted_iterations(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=3)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        assert w._iters == ["iter_001", "iter_002", "iter_003"]

    def test_slider_max_matches_iteration_count(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=5)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        assert w._slider.maximum() == 4  # 0-indexed


class TestPolling:
    """Polling timer behavior."""

    def test_new_iteration_updates_list(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        assert len(w._iters) == 2

        # Simulate adding a new iteration
        adaptive._children["iter_003"] = _make_iter_container()
        w._poll_tick()

        assert len(w._iters) == 3
        assert "iter_003" in w._iters

    def test_stale_polling_stops_timer(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        # No new data — tick 3 times
        w._poll_tick()
        w._poll_tick()
        assert w._poll_timer.isActive()
        w._poll_tick()
        assert not w._poll_timer.isActive()

    def test_new_data_resets_stale_count(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        w._poll_tick()
        w._poll_tick()
        assert w._stale_count == 2

        # Add new iteration
        adaptive._children["iter_003"] = _make_iter_container()
        w._poll_tick()
        assert w._stale_count == 0


class TestSliderAutoFollow:
    """Slider auto-follow behavior."""

    def test_auto_advances_when_at_end(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        # Slider should be at end (index 1)
        assert w._slider.value() == 1

        # Add a new iteration and poll
        adaptive._children["iter_003"] = _make_iter_container()
        w._poll_tick()

        # Should have advanced to index 2
        assert w._slider.value() == 2

    def test_stays_put_when_not_at_end(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        # Move slider back to index 0
        w._slider.setValue(0)

        # Add a new iteration and poll
        adaptive._children["iter_003"] = _make_iter_container()
        w._poll_tick()

        # Should stay at index 0
        assert w._slider.value() == 0


class TestGetStreams:
    """get_streams returns the expected list."""

    def test_returns_adaptive(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_container(n_iters=1)
        run = _make_run(adaptive)
        w.set_run(run)

        assert w.get_streams() == ["adaptive"]


class TestClassAttributes:
    """Verify class-level metadata."""

    def test_viz_name(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        assert AdaptiveHeatmapVisualization.viz_name == "adaptive_heatmap"

    def test_viz_display_name(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        assert AdaptiveHeatmapVisualization.viz_display_name == "Adaptive Heatmap"
