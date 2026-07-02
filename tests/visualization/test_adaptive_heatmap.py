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
    target_n_max: int = 2,
    target_dim: int = 2,
    target_valid_per_iter: list[int] | None = None,
    target_legacy_1d: bool = False,
) -> FakeContainer:
    """Build a fake adaptive stream matching the real zarr-array layout.

    Children are field-level arrays with shape ``(n_iters, flat_size)``.
    Grid config and data_keys live in ``.metadata``.

    Targets storage parallels the tsuchinoko writer: a flat
    ``(n_iters, target_n_max * target_dim)`` array with unused rows
    NaN-padded.  ``target_valid_per_iter[i]`` controls how many rows of
    iteration ``i`` are populated (defaults to all rows).  Setting
    ``target_legacy_1d=True`` emits the pre-fix raveled layout
    (no ``target_shape`` metadata) for the back-compat path.
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
        valid_counts = target_valid_per_iter or [target_n_max] * n_iters
        buf = np.full(
            (n_iters, target_n_max, target_dim), np.nan, dtype=float,
        )
        for i, n_valid in enumerate(valid_counts):
            n_valid = min(int(n_valid), target_n_max)
            if n_valid > 0:
                buf[i, :n_valid] = np.random.rand(n_valid, target_dim) * 50
        flat = buf.reshape(n_iters, target_n_max * target_dim)
        children["targets"] = FakeArray(flat)
        if not target_legacy_1d:
            data_keys["targets"] = {
                "shape": [target_n_max * target_dim],
                "target_shape": [target_n_max, target_dim],
            }

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
def _patch_for_tests(monkeypatch):
    """Patch fetch_frame so tests work with FakeArray (no HTTP).
    Stub _start_subscription since FakeContainer has no subscribe().
    """
    monkeypatch.setattr(
        "lightfall.utils.tiled_helpers.fetch_frame",
        _fake_fetch_frame,
    )
    monkeypatch.setattr(
        "lightfall.visualization.widgets.adaptive.heatmap."
        "AdaptiveHeatmapVisualization._start_subscription",
        lambda self: None,
    )
    yield


# ===========================================================================
# Tests
# ===========================================================================


class TestCanHandle:
    """can_handle scoring tests."""

    def test_valid_2d_tsuchinoko_run(self):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = _make_adaptive_stream(n_iters=2)
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 90

    def test_returns_zero_for_no_adaptive(self):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        run = _make_run(adaptive=None)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_returns_zero_for_wrong_engine(self):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = FakeContainer(
            children={},
            metadata={"adaptive_engine": "other"},
        )
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_returns_zero_for_3d_grid(self):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = _make_adaptive_stream(n_iters=1, has_z=True)
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_returns_zero_for_missing_metadata(self):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = FakeContainer(children={}, metadata={})
        run = _make_run(adaptive)
        assert AdaptiveHeatmapVisualization.can_handle(run) == 0

    def test_handles_exception_gracefully(self):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        assert AdaptiveHeatmapVisualization.can_handle(None) == 0
        assert AdaptiveHeatmapVisualization.can_handle("garbage") == 0


class TestSetRunAndFields:
    """set_run + get_fields integration."""

    def test_get_fields_returns_present_arrays(self, qtbot):
        from lightfall.visualization.widgets.adaptive.heatmap import (
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
        from lightfall.visualization.widgets.adaptive.heatmap import (
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
        from lightfall.visualization.widgets.adaptive.heatmap import (
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
        from lightfall.visualization.widgets.adaptive.heatmap import (
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
        from lightfall.visualization.widgets.adaptive.heatmap import (
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
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_stream(n_iters=1, grid_res=8)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        # grid_shape = [Nx, Ny] = [8, 8] → frame_shape (Ny, Nx) under row-major.
        assert w._frame_shape == (8, 8)

    def test_frame_orientation_axis1_is_x(self, qtbot):
        """Displayed frame axis-1 must track grid_x, not grid_y.

        The tsuchinoko writer ravels ``meshgrid(*grids, indexing='ij')``
        so ``flat[i*Ny + j] = posterior(grid_x[i], grid_y[j])``.  With
        the global row-major axis order, array axis 1 maps to plot-x, so
        the displayed frame is transposed to shape ``(Ny, Nx)`` with
        axis 1 indexing grid_x — otherwise the heatmap appears transposed
        relative to the scatter overlays.
        """
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        N = 5  # grid resolution (square)
        # Deterministic posterior depends only on the x-index i.
        # Under the ij-meshgrid convention, position k=i*N+j carries
        # the value i, so reshape((N, N)).T must yield arr[j, i] = i.
        flat = np.zeros((1, N * N), dtype=float)
        for i in range(N):
            for j in range(N):
                flat[0, i * N + j] = float(i)

        adaptive = _make_adaptive_stream(n_iters=1, grid_res=N)
        adaptive._children["posterior_mean"] = FakeArray(flat)

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)
        w.set_run(_make_run(adaptive))
        w.set_stream("adaptive")

        frame = w._image_view._fetch_func(0)
        assert frame.shape == (N, N)
        # Axis 1 = grid_x: columns must be constant, varying with i.
        for i in range(N):
            assert np.all(frame[:, i] == float(i)), (
                f"column {i} not constant at {i}: {frame[:, i]}"
            )

    def test_grid_rect_matches_axes(self, qtbot):
        """setRect must put grid_x extent on x-axis, grid_y on y-axis.

        ``_make_adaptive_stream`` spans grid_x ∈ [0, 100] and
        grid_y ∈ [0, 50] — distinct ranges so a swap is detectable.
        """
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        adaptive = _make_adaptive_stream(n_iters=1, grid_res=6)

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)
        w.set_run(_make_run(adaptive))
        w.set_stream("adaptive")

        image_item = w._image_view.imageItem
        rect = image_item.mapRectToParent(image_item.boundingRect())
        assert rect.x() == pytest.approx(0.0)
        assert rect.width() == pytest.approx(100.0)
        assert rect.y() == pytest.approx(0.0)
        assert rect.height() == pytest.approx(50.0)

    def test_y_axis_is_positive_up(self, qtbot):
        """Heatmap uses math-convention Y (positive up).

        pyqtgraph's ``ImageView.__init__`` calls ``view.invertY()``
        unconditionally; the heatmap viz must undo that so scientific
        (x, y) motor coordinates display the natural way.
        """
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)
        assert w._image_view.getView().getViewBox().yInverted() is False


class TestNewIterationDetection:
    """_check_for_new_iterations behavior."""

    def test_no_op_when_count_unchanged(self, qtbot):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_stream(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        old_index = w._current_index
        w._apply_new_iterations({"n": w._adaptive._children["posterior_mean"].shape[0], "meas_x": None, "meas_y": None})
        assert w._current_index == old_index  # unchanged

    def test_detects_new_iterations(self, qtbot):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)

        adaptive = _make_adaptive_stream(n_iters=2)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")

        assert w._n_iterations == 2

        # Grow the posterior_mean array (simulating new iteration)
        old_data = adaptive._children["posterior_mean"]._data
        new_row = np.random.rand(1, old_data.shape[1])
        adaptive._children["posterior_mean"] = FakeArray(
            np.vstack([old_data, new_row])
        )
        w._apply_new_iterations({"n": w._adaptive._children["posterior_mean"].shape[0], "meas_x": None, "meas_y": None})
        assert w._n_iterations == 3


class TestTimelineScrubbing:
    """Timeline (ImageView) replaces the old slider."""

    def test_initial_position_is_last_iteration(self, qtbot):
        from lightfall.visualization.widgets.adaptive.heatmap import (
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
        from lightfall.visualization.widgets.adaptive.heatmap import (
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
        w._apply_new_iterations({"n": w._adaptive._children["posterior_mean"].shape[0], "meas_x": None, "meas_y": None})

        assert w._current_index == 2  # advanced to new end


class TestGetStreams:
    """get_streams returns the expected list."""

    def test_returns_adaptive(self, qtbot):
        from lightfall.visualization.widgets.adaptive.heatmap import (
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
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        assert AdaptiveHeatmapVisualization.viz_name == "adaptive_heatmap"

    def test_viz_display_name(self):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        assert AdaptiveHeatmapVisualization.viz_display_name == "Adaptive Heatmap"


class TestTargetOverlay:
    """_apply_target_scatter handles padded, ragged, and legacy shapes."""

    def _setup(self, qtbot, **stream_kwargs):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        w = AdaptiveHeatmapVisualization()
        qtbot.addWidget(w)
        adaptive = _make_adaptive_stream(**stream_kwargs)
        run = _make_run(adaptive)
        w.set_run(run)
        w.set_stream("adaptive")
        return w

    def test_padded_targets_with_full_n_plots_all(self, qtbot):
        # 3 targets per iter, all valid → 3 points on the scatter
        w = self._setup(
            qtbot, n_iters=2, target_n_max=3,
            target_valid_per_iter=[3, 3],
        )
        flat = np.asarray(w._adaptive["targets"][0]).reshape(3, 2)
        w._apply_target_scatter(flat.ravel(), target_shape=(3, 2))

        data = w._target_scatter.getData()
        assert len(data[0]) == 3
        np.testing.assert_allclose(data[0], flat[:, 0])
        np.testing.assert_allclose(data[1], flat[:, 1])

    def test_padded_targets_with_partial_n_masks_nan_rows(self, qtbot):
        # N_max=3, only 1 valid row → exactly 1 point rendered
        w = self._setup(
            qtbot, n_iters=1, target_n_max=3,
            target_valid_per_iter=[1],
        )
        flat = np.asarray(w._adaptive["targets"][0])
        w._apply_target_scatter(flat, target_shape=(3, 2))

        data = w._target_scatter.getData()
        assert len(data[0]) == 1
        expected = flat.reshape(3, 2)[0]
        np.testing.assert_allclose([data[0][0], data[1][0]], expected)

    def test_all_nan_iteration_clears_scatter(self, qtbot):
        w = self._setup(
            qtbot, n_iters=1, target_n_max=2,
            target_valid_per_iter=[0],
        )
        flat = np.asarray(w._adaptive["targets"][0])
        w._apply_target_scatter(flat, target_shape=(2, 2))

        data = w._target_scatter.getData()
        # ScatterPlotItem.getData() returns empty arrays after clear()
        assert data[0] is None or len(data[0]) == 0

    def test_legacy_1d_raveled_falls_back_to_pairs(self, qtbot):
        # Legacy: no target_shape metadata, raveled (N*2,) per iteration
        w = self._setup(
            qtbot, n_iters=1, target_n_max=3,
            target_valid_per_iter=[3], target_legacy_1d=True,
        )
        # Without metadata we mimic an old run with no NaN padding
        legacy_flat = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        w._apply_target_scatter(legacy_flat, target_shape=None)

        data = w._target_scatter.getData()
        assert len(data[0]) == 3
        np.testing.assert_allclose(data[0], [1.0, 3.0, 5.0])
        np.testing.assert_allclose(data[1], [2.0, 4.0, 6.0])

    def test_target_logical_shape_reads_descriptor_metadata(self, qtbot):
        w = self._setup(
            qtbot, n_iters=1, target_n_max=4, target_dim=2,
        )
        assert w._target_logical_shape() == (4, 2)

    def test_target_logical_shape_none_for_legacy(self, qtbot):
        w = self._setup(
            qtbot, n_iters=1, target_legacy_1d=True,
        )
        assert w._target_logical_shape() is None
