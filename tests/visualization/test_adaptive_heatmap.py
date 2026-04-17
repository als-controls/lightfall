"""Tests for AdaptiveHeatmapVisualization."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Fake Tiled helpers (dict-backed stand-ins)
# ---------------------------------------------------------------------------


class FakeArray:
    """Fake Tiled array node: supports indexing, .shape, .read()."""

    def __init__(self, data: np.ndarray):
        self._data = data

    @property
    def shape(self):
        return self._data.shape

    @property
    def ndim(self):
        return self._data.ndim

    def __getitem__(self, key):
        return self._data[key]

    def read(self):
        return self._data.copy()


class FakeContainer:
    """Fake Tiled container node: supports [], .keys(), iter, in, .metadata."""

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

    def __iter__(self):
        return iter(self._children)

    def keys(self):
        return list(self._children.keys())

    @property
    def metadata(self):
        return self._metadata


def _make_adaptive_stream(
    n_iters: int = 3,
    grid_res: int = 10,
    *,
    has_z: bool = False,
    has_mean: bool = True,
    has_var: bool = True,
    has_acq: bool = True,
    has_targets: bool = True,
) -> FakeContainer:
    """Build a fake adaptive stream matching the real zarr-array layout.

    Children are field-level arrays with shape ``(n_iters, flat_size)``.
    Grid config and data_keys live in ``.metadata``.
    """
    flat_size = grid_res * grid_res
    children: dict[str, Any] = {}
    data_keys: dict[str, Any] = {}

    if has_mean:
        children["posterior_mean"] = FakeArray(
            np.random.rand(n_iters, flat_size)
        )
        data_keys["posterior_mean"] = {"grid_shape": [grid_res, grid_res]}
    if has_var:
        children["posterior_variance"] = FakeArray(
            np.random.rand(n_iters, flat_size)
        )
    if has_acq:
        children["acquisition_function"] = FakeArray(
            np.random.rand(n_iters, flat_size)
        )
    if has_targets:
        # Each iteration gets a (2, 2) target array → shape (n_iters, 2, 2)
        children["targets"] = FakeArray(
            np.random.rand(n_iters, 2, 2) * 50
        )

    # Grid config stored in metadata.configuration.tsuchinoko.data
    grid_config: dict[str, Any] = {
        "evaluation_grid_x": np.linspace(0, 100, grid_res).tolist(),
        "evaluation_grid_y": np.linspace(0, 50, grid_res).tolist(),
    }
    if has_z:
        grid_config["evaluation_grid_z"] = np.linspace(0, 25, grid_res).tolist()

    metadata = {
        "adaptive_engine": "tsuchinoko",
        "configuration": {"tsuchinoko": {"data": grid_config}},
        "data_keys": data_keys,
    }
    return FakeContainer(children=children, metadata=metadata)


def _make_run(
    adaptive: FakeContainer | None = None,
    *,
    primary_events: dict[str, np.ndarray] | None = None,
    dim_fields: tuple[str, str] = ("motor_x", "motor_y"),
) -> FakeContainer:
    """Build a top-level fake BlueskyRun."""
    children: dict[str, Any] = {}
    if adaptive is not None:
        children["adaptive"] = adaptive

    if primary_events is not None:
        primary_children = {k: FakeArray(v) for k, v in primary_events.items()}
        primary = FakeContainer(children=primary_children)
        children["primary"] = primary

    start_md: dict[str, Any] = {}
    if dim_fields:
        dims = [([dim_fields[0]], "primary"), ([dim_fields[1]], "primary")]
        start_md = {"hints": {"dimensions": dims}}

    return FakeContainer(children=children, metadata={"start": start_md})


def _fake_fetch_frame(dataset, index):
    """Stand-in for tiled_helpers.fetch_frame — plain indexing for FakeArrays."""
    index = int(max(0, min(index, dataset.shape[0] - 1)))
    return np.asarray(dataset[index])


@pytest.fixture(autouse=True)
def _patch_fetch_frame():
    """Patch fetch_frame so tests work with FakeArray (no HTTP)."""
    with patch(
        "lucid.utils.tiled_helpers.fetch_frame",
        side_effect=_fake_fetch_frame,
    ):
        yield


# ===========================================================================
# Tests
# ===========================================================================


class TestCanHandle:
    """can_handle scoring tests."""

    def test_valid_2d_tsuchinoko_run(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = _make_adaptive_stream(n_iters=2)
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
            children={},
            metadata={"adaptive_engine": "other"},
        )
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_returns_zero_for_3d_grid(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = _make_adaptive_stream(n_iters=1, has_z=True)
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_returns_zero_for_missing_metadata(self):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = FakeContainer(children={}, metadata={})
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

        adaptive = _make_adaptive_stream(n_iters=2)
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

        adaptive = _make_adaptive_stream(n_iters=1, has_acq=False)
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

        adaptive = _make_adaptive_stream(n_iters=1)
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

        adaptive = _make_adaptive_stream(n_iters=1)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        w.set_field("posterior_mean")
        assert w._field_name == "posterior_mean"

        w.set_field("posterior_variance")
        assert w._field_name == "posterior_variance"

    def test_set_field_configures_image_view(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_stream(n_iters=3, grid_res=5)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        # Should have configured LazyImageView with 3 iterations
        assert w._n_iterations == 3
        assert w._image_view.frame_count == 3
        # Should be on the latest iteration
        assert w._current_index == 2

    def test_set_field_sets_frame_shape(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_stream(n_iters=1, grid_res=8)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        # grid_shape = [8, 8], transposed for col-major → (8, 8)
        assert w._frame_shape == (8, 8)


class TestPolling:
    """Polling timer behavior."""

    def test_stale_polling_stops_timer(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_stream(n_iters=2)
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

        adaptive = _make_adaptive_stream(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        w._poll_tick()
        w._poll_tick()
        assert w._stale_count == 2

        # Grow the posterior_mean array (simulating new iteration)
        old_data = adaptive._children["posterior_mean"]._data
        new_row = np.random.rand(1, old_data.shape[1])
        adaptive._children["posterior_mean"] = FakeArray(
            np.vstack([old_data, new_row])
        )
        w._poll_tick()
        assert w._stale_count == 0


class TestTimelineScrubbing:
    """Timeline (ImageView) replaces the old slider."""

    def test_initial_position_is_last_iteration(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_stream(n_iters=5)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        assert w._current_index == 4  # 0-indexed last

    def test_auto_advances_when_at_end(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_stream(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        assert w._current_index == 1  # at end

        # Grow the array
        old_data = adaptive._children["posterior_mean"]._data
        new_row = np.random.rand(1, old_data.shape[1])
        adaptive._children["posterior_mean"] = FakeArray(
            np.vstack([old_data, new_row])
        )
        w._poll_tick()

        assert w._current_index == 2  # advanced to new end


class TestGetStreams:
    """get_streams returns the expected list."""

    def test_returns_adaptive(self, qtbot):
        from lucid.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_stream(n_iters=1)
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
