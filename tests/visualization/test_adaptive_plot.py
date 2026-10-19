"""Tests for AdaptivePlotVisualization (GP hyperparameters over iterations).

The fakes mirror the layout Tsuchinoko's TiledPublisher actually writes:
the ``adaptive`` stream is a Bluesky event stream whose descriptor
``data_keys`` carry ``source="tsuchinoko"``, and ``hyperparameters`` is a
zarr array child of shape ``(n_iterations, n_hp)`` (one row per event).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Fake Tiled helpers
# ---------------------------------------------------------------------------


class FakeArray:
    def __init__(self, data: np.ndarray):
        self._data = np.asarray(data)

    @property
    def shape(self):
        return self._data.shape

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

    def __iter__(self):
        return iter(self._children)

    def keys(self):
        return list(self._children.keys())

    @property
    def metadata(self):
        return self._metadata


def _data_keys(*, has_hp: bool = True, source: str = "tsuchinoko") -> dict:
    dks: dict[str, Any] = {
        "targets": {"dtype": "array", "shape": [2], "source": source},
    }
    if has_hp:
        dks["hyperparameters"] = {"dtype": "array", "shape": [20], "source": source}
    return dks


def _make_adaptive(
    hp: np.ndarray | None = None,
    *,
    has_hp: bool = True,
    source: str = "tsuchinoko",
    metadata: dict | None = None,
) -> FakeContainer:
    children: dict[str, Any] = {"targets": FakeArray(np.zeros((2, 2)))}
    if has_hp:
        if hp is None:
            hp = np.array([[1.0, 2.0, 3.0], [1.5, 2.5, 3.5]])
        children["hyperparameters"] = FakeArray(hp)
    md = metadata if metadata is not None else {
        "data_keys": _data_keys(has_hp=has_hp, source=source),
    }
    return FakeContainer(children=children, metadata=md)


def _make_run(adaptive: FakeContainer | None = None) -> FakeContainer:
    children: dict[str, Any] = {}
    if adaptive is not None:
        children["adaptive"] = adaptive
    return FakeContainer(children=children, metadata={"start": {}})


@pytest.fixture
def viz(qtbot):
    from lightfall.visualization.widgets.adaptive.plot import (
        AdaptivePlotVisualization,
    )

    w = AdaptivePlotVisualization()
    qtbot.addWidget(w)
    return w


def _visible_lines(w) -> list:
    return [line for line in w._lines if line.isVisible()]


# ===========================================================================
# Scoring
# ===========================================================================


class TestCanHandle:
    def _score(self, run) -> int:
        from lightfall.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        return AdaptivePlotVisualization.can_handle(run)

    def test_real_layout_with_hyperparameters_scores_70(self):
        assert self._score(_make_run(_make_adaptive())) == 70

    def test_legacy_adaptive_engine_key_still_recognised(self):
        adaptive = _make_adaptive(metadata={
            "adaptive_engine": "tsuchinoko",
            "data_keys": {"hyperparameters": {"dtype": "array", "shape": [20]}},
        })
        assert self._score(_make_run(adaptive)) == 70

    def test_scores_below_adaptive_heatmap(self):
        from lightfall.visualization.widgets.adaptive.heatmap import (
            AdaptiveHeatmapVisualization,
        )

        run = _make_run(_make_adaptive())
        # Heatmap needs grids to score; give it a 2D configuration.
        run["adaptive"]._metadata["configuration"] = {
            "tsuchinoko": {"data": {"evaluation_grid_x": [0, 1],
                                    "evaluation_grid_y": [0, 1]}},
        }
        assert 0 < self._score(run) < AdaptiveHeatmapVisualization.can_handle(run)

    def test_zero_without_hyperparameters(self):
        assert self._score(_make_run(_make_adaptive(has_hp=False))) == 0

    def test_zero_for_other_source(self):
        assert self._score(_make_run(_make_adaptive(source="other"))) == 0

    def test_zero_without_adaptive_stream(self):
        assert self._score(_make_run(None)) == 0

    def test_garbage_is_zero(self):
        assert self._score(None) == 0
        assert self._score("nope") == 0


# ===========================================================================
# Reading
# ===========================================================================


class TestReadHyperparameters:
    def _read(self, adaptive):
        from lightfall.visualization.widgets.adaptive.plot import (
            read_hyperparameters,
        )

        return read_hyperparameters(adaptive)

    def test_returns_iterations_by_components(self):
        values = self._read(_make_adaptive())
        assert values.shape == (2, 3)
        np.testing.assert_allclose(values[1], [1.5, 2.5, 3.5])

    def test_drops_all_nan_padding_columns(self):
        hp = np.full((3, 20), np.nan)
        hp[:, :3] = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
        values = self._read(_make_adaptive(hp))
        assert values.shape == (3, 3)

    def test_keeps_nan_rows_for_failed_iterations(self):
        hp = np.array([[1.0, 2.0], [np.nan, np.nan], [3.0, 4.0]])
        values = self._read(_make_adaptive(hp))
        assert values.shape == (3, 2)
        assert np.isnan(values[1]).all()

    def test_one_dimensional_is_single_component(self):
        values = self._read(_make_adaptive(np.array([1.0, 2.0, 3.0])))
        assert values.shape == (3, 1)

    def test_falls_back_to_base_container(self):
        inner = _make_adaptive()
        outer = FakeContainer(children={}, metadata=inner.metadata)
        outer.base = inner
        assert self._read(outer).shape == (2, 3)

    def test_missing_field_raises_keyerror(self):
        with pytest.raises(KeyError):
            self._read(_make_adaptive(has_hp=False))


# ===========================================================================
# Widget behaviour
# ===========================================================================


class TestStreamsAndFields:
    def test_get_streams(self, viz):
        viz.set_run(_make_run(_make_adaptive()))
        assert viz.get_streams() == ["adaptive"]

    def test_get_fields_from_descriptor(self, viz):
        viz.set_run(_make_run(_make_adaptive()))
        viz.set_stream("adaptive")
        assert viz.get_fields() == ["hyperparameters"]

    def test_get_fields_empty_without_hyperparameters(self, viz):
        viz.set_run(_make_run(_make_adaptive(has_hp=False)))
        viz.set_stream("adaptive")
        assert viz.get_fields() == []

    def test_set_stream_without_adaptive_is_harmless(self, viz):
        viz.set_run(_make_run(None))
        viz.set_stream("adaptive")
        assert viz.get_fields() == []
        viz.refresh()  # no adaptive -> no-op, must not raise


class TestRendering:
    def test_one_line_per_component(self, viz):
        viz._apply_hyperparameters(np.array([[1.0, 2.0, 3.0], [1.5, 2.5, 3.5]]))
        assert len(_visible_lines(viz)) == 3
        x, y = viz._lines[2].getData()
        np.testing.assert_allclose(x, [1, 2])  # 1-based iteration, like the heatmap
        np.testing.assert_allclose(y, [3.0, 3.5])

    def test_lines_use_themed_series_pens(self, viz):
        from lightfall.visualization import pg

        viz._apply_hyperparameters(np.array([[1.0, 2.0]]))
        assert all(isinstance(line, pg.PlotDataItem) for line in viz._lines)
        assert (viz._lines[0].opts["pen"].color().name()
                == pg.mkPen(pg.series_pen(0)).color().name())

    def test_fewer_components_hides_excess_lines(self, viz):
        viz._apply_hyperparameters(np.array([[1.0, 2.0, 3.0]]))
        viz._apply_hyperparameters(np.array([[1.0, 2.0]]))
        assert len(_visible_lines(viz)) == 2

    def test_refresh_preserves_legend_hidden_line(self, viz):
        viz._apply_hyperparameters(np.array([[1.0, 2.0]]))
        viz._lines[0].setVisible(False)  # what a legend click does
        viz._apply_hyperparameters(np.array([[1.0, 2.0], [1.1, 2.1]]))
        assert not viz._lines[0].isVisible()
        assert viz._lines[1].isVisible()

    def test_legend_names(self, viz):
        viz._apply_hyperparameters(np.array([[1.0, 2.0]]))
        assert [line.name() for line in viz._lines] == ["hp[0]", "hp[1]"]

    def test_log_y_toggle(self, viz):
        plot_item = viz._plot_widget.getPlotItem()
        assert not plot_item.ctrl.logYCheck.isChecked()
        viz._log_btn.setChecked(True)
        assert plot_item.ctrl.logYCheck.isChecked()
        viz._log_btn.setChecked(False)
        assert not plot_item.ctrl.logYCheck.isChecked()


class TestAsyncLoading:
    def test_set_stream_loads_off_thread(self, viz, qtbot):
        viz.set_run(_make_run(_make_adaptive()))
        viz.set_stream("adaptive")
        qtbot.waitUntil(lambda: len(_visible_lines(viz)) == 3, timeout=3000)
        assert viz._n_iterations == 2

    def test_refresh_picks_up_new_iterations(self, viz, qtbot):
        adaptive = _make_adaptive()
        viz.set_run(_make_run(adaptive))
        viz.set_stream("adaptive")
        qtbot.waitUntil(lambda: viz._n_iterations == 2, timeout=3000)

        adaptive._children["hyperparameters"] = FakeArray(
            np.array([[1.0, 2.0, 3.0], [1.5, 2.5, 3.5], [1.7, 2.7, 3.7]])
        )
        viz.refresh()
        qtbot.waitUntil(lambda: viz._n_iterations == 3, timeout=3000)
        x, _ = viz._lines[0].getData()
        np.testing.assert_allclose(x, [1, 2, 3])

    def test_read_runs_off_gui_thread(self, viz, qtbot, monkeypatch):
        from lightfall.utils.threads import is_main_thread
        from lightfall.visualization.widgets.adaptive import plot as plot_mod

        seen: list[bool] = []
        real = plot_mod.read_hyperparameters

        def spy(adaptive):
            seen.append(is_main_thread())
            return real(adaptive)

        monkeypatch.setattr(plot_mod, "read_hyperparameters", spy)
        viz.set_run(_make_run(_make_adaptive()))
        viz.set_stream("adaptive")
        qtbot.waitUntil(lambda: viz._n_iterations == 2, timeout=3000)
        assert seen and not any(seen)

    def test_read_failure_keeps_widget_alive(self, viz, qtbot, monkeypatch):
        from lightfall.visualization.widgets.adaptive import plot as plot_mod

        def boom(adaptive):
            raise RuntimeError("tiled down")

        monkeypatch.setattr(plot_mod, "read_hyperparameters", boom)
        viz.set_run(_make_run(_make_adaptive()))
        viz.set_stream("adaptive")
        qtbot.waitUntil(lambda: viz._future is None, timeout=3000)
        assert viz._n_iterations == 0


class TestClassAttributes:
    def test_names(self):
        from lightfall.visualization.widgets.adaptive.plot import (
            AdaptivePlotVisualization,
        )

        assert AdaptivePlotVisualization.viz_name == "adaptive_plot"
        assert AdaptivePlotVisualization.viz_display_name == "GP Hyperparameters"
