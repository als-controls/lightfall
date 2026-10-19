"""Characterization tests for Auto-mode visualization selection.

``VisualizationPanel._pick_widget_class`` scores every class from
``_widget_classes()`` with ``can_handle(run)`` and picks the highest score
(unless the viz combo names a specific widget). These tests drive that real
selection path with Bluesky start/descriptor documents wrapped in a fake
Tiled run, pinning which visualization wins for each data shape.

Ported from the Feb 2026 ``DocumentProcessor``/``SelectionEngine`` suite;
the expectations that changed when those were replaced are noted inline.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from lightfall.ui.panels.visualization_panel import VisualizationPanel, _widget_classes
from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.registry import VisualizationRegistry

from .document_generators import (
    DATA_DIR,
    FakeRun,
    all_fixture_names,
    fake_run,
    fake_run_from_pair,
    load_fixture,
    make_array_data_key,
    make_descriptor_doc,
    make_scalar_data_key,
    make_start_doc,
)

FIXTURES = [
    "1d_scalar_scan",
    "1d_with_2d_array",
    "2d_nonrectilinear_scalar",
    "2d_rectilinear_scalar",
    "count_only",
    "scan_with_3d_volume",
]


@pytest.fixture(autouse=True)
def _clean_registry():
    """Keep plugin-contributed viz classes out of the built-in ranking."""
    VisualizationRegistry.reset()
    yield
    VisualizationRegistry.reset()


def _pick(run: Any, manual: str | None = None) -> type[BaseVisualization] | None:
    """Run the panel's real selection logic without constructing the panel."""
    stub = SimpleNamespace(_viz_combo=SimpleNamespace(currentData=lambda: manual))
    return VisualizationPanel._pick_widget_class(stub, _widget_classes(), run)


def _scores(run: Any) -> dict[str, int]:
    return {cls.viz_name: cls.can_handle(run) for cls in _widget_classes()}


def _ranking(run: Any) -> list[tuple[str, int]]:
    """Non-zero scores, best first (what the old SelectionEngine returned)."""
    ranked = [(name, s) for name, s in _scores(run).items() if s > 0]
    return sorted(ranked, key=lambda item: item[1], reverse=True)


# =============================================================================
# Canonical cases from the JSON fixtures
# =============================================================================


@pytest.mark.parametrize("layout", ["tiled_writer", "descriptors"])
@pytest.mark.parametrize("name", FIXTURES)
def test_auto_selection(name: str, layout: str) -> None:
    """Each fixture picks its expected viz with its expected score, for both
    the TiledWriter and the databroker-style descriptor layouts."""
    pair = load_fixture(name)
    run = fake_run_from_pair(pair, layout=layout)

    winner = _pick(run)

    assert winner is not None
    assert winner.viz_name == pair.expected_viz
    assert winner.can_handle(run) == pair.expected_score


class TestPlot1DSelection:
    def test_1d_scalar_ranking(self) -> None:
        run = fake_run_from_pair(load_fixture("1d_scalar_scan"))
        assert _ranking(run) == [("plot_1d", 80), ("table", 40)]

    def test_count_with_time_placeholder_dimension(self) -> None:
        """Real ``count`` plans carry bluesky's default ``time`` dimension."""
        start = make_start_doc(
            plan_name="count",
            num_points=5,
            hints={"dimensions": [[["time"], "primary"]]},
        )
        desc = make_descriptor_doc(
            start_uid=start["uid"],
            data_keys={"det": make_scalar_data_key("det")},
            hints={"det": {"fields": ["det"]}},
        )
        winner = _pick(fake_run(start, desc))
        assert winner is not None and winner.viz_name == "plot_1d"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Plot1DVisualization.can_handle returns 80 for any shape-[] data "
            "key, including the scanned motor and non-numeric fields, while "
            "get_fields excludes both; a motor + string-detector scan opens an "
            "empty 1D plot instead of the table (old suite: score < 80)."
        ),
    )
    def test_1d_without_numeric_dependent_field_does_not_pick_plot(self) -> None:
        start = make_start_doc(
            motors=["motor"],
            num_points=10,
            hints={"dimensions": [[["motor"], "primary"]]},
        )
        desc = make_descriptor_doc(
            start_uid=start["uid"],
            data_keys={
                "motor": make_scalar_data_key("motor"),
                "det": make_scalar_data_key("det", dtype="string"),
            },
        )
        winner = _pick(fake_run(start, desc))
        assert winner is not None and winner.viz_name == "table"


class TestTwoDimensionalSelection:
    def test_heatmap_beats_scatter_for_rectilinear(self) -> None:
        run = fake_run_from_pair(load_fixture("2d_rectilinear_scalar"))
        assert _ranking(run) == [("heatmap", 85), ("scatter", 50), ("table", 40)]

    def test_scatter_beats_heatmap_for_nonrectilinear(self) -> None:
        run = fake_run_from_pair(load_fixture("2d_nonrectilinear_scalar"))
        assert _ranking(run) == [("scatter", 70), ("table", 40), ("heatmap", 30)]

    def test_2d_viz_score_zero_for_1d(self) -> None:
        scores = _scores(fake_run_from_pair(load_fixture("1d_scalar_scan")))
        assert scores["heatmap"] == 0
        assert scores["scatter"] == 0

    def test_plot_1d_scores_zero_for_2d(self) -> None:
        scores = _scores(fake_run_from_pair(load_fixture("2d_rectilinear_scalar")))
        assert scores["plot_1d"] == 0


class TestImageSelection:
    def test_motor_scan_with_camera_prefers_scan_viewer(self) -> None:
        """Was ``image_stack`` (75) before Scan Viewer (90) existed; Image
        Stack and Plot 1D remain available as lower-ranked choices."""
        run = fake_run_from_pair(load_fixture("1d_with_2d_array"))
        assert _ranking(run) == [
            ("scan_viewer", 90),
            ("plot_1d", 80),
            ("image_stack", 75),
            ("table", 40),
        ]

    def test_image_stack_detects_2d_array_without_hints(self) -> None:
        start = make_start_doc(
            motors=["motor"],
            num_points=10,
            hints={"dimensions": [[["motor"], "primary"]]},
        )
        desc = make_descriptor_doc(
            start_uid=start["uid"],
            data_keys={
                "motor": make_scalar_data_key("motor"),
                "camera": make_array_data_key("camera", shape=[512, 512]),
            },
            hints={},
        )
        assert _scores(fake_run(start, desc))["image_stack"] == 75

    def test_3d_per_point_field_goes_to_scan_viewer(self) -> None:
        """The old ``volume`` viz was removed in the tiled-only rewrite
        (a878016); 3-D per-point data now opens in Scan Viewer."""
        run = fake_run_from_pair(load_fixture("scan_with_3d_volume"))
        assert "volume" not in _scores(run)
        winner = _pick(run)
        assert winner is not None and winner.viz_name == "scan_viewer"

    def test_image_count_selects_image_stack(self) -> None:
        start = make_start_doc(
            plan_name="count",
            num_points=10,
            hints={"dimensions": [[["time"], "primary"]]},
        )
        desc = make_descriptor_doc(
            start_uid=start["uid"],
            data_keys={"cam_image": make_array_data_key("cam_image", shape=[256, 256])},
            hints={"cam": {"fields": ["cam_image"]}},
        )
        winner = _pick(fake_run(start, desc))
        assert winner is not None and winner.viz_name == "image_stack"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "A count of an area detector that also records scalar stats picks "
            "Plot 1D (80) over Image Stack (75); 879479d made ScanViewer ignore "
            "the 'time' dimension precisely so count plans defer to Image Stack."
        ),
    )
    def test_image_count_with_scalar_stats_selects_image_stack(self) -> None:
        start = make_start_doc(
            plan_name="count",
            num_points=10,
            hints={"dimensions": [[["time"], "primary"]]},
        )
        desc = make_descriptor_doc(
            start_uid=start["uid"],
            data_keys={
                "cam_image": make_array_data_key("cam_image", shape=[256, 256]),
                "cam_stats_total": make_scalar_data_key("cam_stats_total"),
            },
            hints={"cam": {"fields": ["cam_image", "cam_stats_total"]}},
        )
        winner = _pick(fake_run(start, desc))
        assert winner is not None and winner.viz_name == "image_stack"

    def test_only_primary_stream_drives_selection(self) -> None:
        """Images in a non-primary stream do not make the run an image run
        (old processor: non-primary descriptors ignored)."""
        pair = load_fixture("1d_scalar_scan")
        baseline = make_descriptor_doc(
            start_uid=pair.start["uid"],
            data_keys={"cam_image": make_array_data_key("cam_image", shape=[64, 64])},
            name="baseline",
        )
        run = fake_run(pair.start, pair.descriptor, baseline)
        scores = _scores(run)
        assert scores["image_stack"] == 0
        assert scores["scan_viewer"] == 0
        assert _pick(run).viz_name == "plot_1d"


class TestTableFallback:
    @pytest.mark.parametrize("name", FIXTURES)
    def test_table_always_scores_40(self, name: str) -> None:
        run = fake_run_from_pair(load_fixture(name))
        assert _scores(run)["table"] == 40

    def test_table_wins_when_nothing_else_applies(self) -> None:
        run = FakeRun({}, {})
        winner = _pick(run)
        assert winner is not None and winner.viz_name == "table"


class TestSelectionMechanics:
    """Panel-side selection rules (replacing SelectionEngine mechanics)."""

    def test_manual_choice_overrides_scores(self) -> None:
        """The viz combo replaces the old per-plan/ndim user preferences."""
        run = fake_run_from_pair(load_fixture("1d_scalar_scan"))
        winner = _pick(run, manual="table")
        assert winner is not None and winner.viz_name == "table"

    def test_unknown_manual_choice_falls_back_to_auto(self) -> None:
        run = fake_run_from_pair(load_fixture("1d_scalar_scan"))
        winner = _pick(run, manual="nonexistent_viz")
        assert winner is not None and winner.viz_name == "plot_1d"

    def test_all_zero_scores_pick_nothing(self) -> None:
        class _Never(BaseVisualization):
            viz_name = "never"

            @staticmethod
            def can_handle(run: Any) -> int:
                return 0

        stub = SimpleNamespace(_viz_combo=SimpleNamespace(currentData=lambda: None))
        assert VisualizationPanel._pick_widget_class(stub, [_Never], FakeRun({}, {})) is None
        assert VisualizationPanel._pick_widget_class(stub, [], FakeRun({}, {})) is None

    @pytest.mark.parametrize("name", FIXTURES)
    def test_scores_within_0_100(self, name: str) -> None:
        run = fake_run_from_pair(load_fixture(name))
        assert all(0 <= s <= 100 for s in _scores(run).values())


# =============================================================================
# Field auto-pick (old DocumentProcessor dependent-field tests)
# =============================================================================


class TestPlot1DFieldOrder:
    def _fields(self, qtbot, name: str) -> list[str]:
        from lightfall.visualization.widgets.plot_1d import Plot1DVisualization

        widget = Plot1DVisualization()
        qtbot.addWidget(widget)
        widget.set_run(fake_run_from_pair(load_fixture(name)))
        widget.set_stream("primary")
        return widget.get_fields()

    def test_motors_excluded_from_fields(self, qtbot) -> None:
        fields = self._fields(qtbot, "1d_scalar_scan")
        assert "motor" not in fields
        assert set(fields) == {"motor_setpoint", "det"}

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Plot1D/Heatmap/ImageStack/ScanViewer read stream "
            "metadata['hints']['fields'], but TiledWriter stores the event-model "
            "descriptor hints ({object: {'fields': [...]}}), so hinted fields are "
            "never ranked first; the 1D scalar scan auto-plots motor_setpoint "
            "instead of the hinted detector."
        ),
    )
    def test_hinted_detector_is_primary_field(self, qtbot) -> None:
        assert self._fields(qtbot, "1d_scalar_scan")[0] == "det"


# =============================================================================
# Fixture files
# =============================================================================


def test_fixture_files_present() -> None:
    assert all_fixture_names() == sorted(FIXTURES)


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_structure(name: str) -> None:
    pair = load_fixture(name)
    assert pair.description == name
    assert {"uid", "plan_name"} <= pair.start.keys()
    assert {"uid", "run_start", "data_keys"} <= pair.descriptor.keys()
    assert pair.descriptor["run_start"] == pair.start["uid"]
    assert (DATA_DIR / f"{name}.json").is_file()
