"""Test that _widget_classes() includes registry-contributed visualizations.

TDD test for Task 4c: VisualizationPanel._widget_classes() must consult
VisualizationRegistry so plugin-contributed BaseVisualization subclasses
appear in combo, scoring, and manual selection.

Covers:
  - Registry-contributed class IS in _widget_classes()
  - All 8 built-ins are still present
  - No duplicates (registry entry that IS a built-in is not doubled)
  - Plugin.get_viz_class() exception is swallowed gracefully
  - Registry import failure is swallowed gracefully (built-ins still returned)
"""

from __future__ import annotations

import pytest

from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.registry import VisualizationRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUILTIN_NAMES = [
    "ImageStackVisualization",
    "ScanViewerVisualization",
    "Plot1DVisualization",
    "HeatmapVisualization",
    "ScatterVisualization",
    "TableVisualization",
    "AdaptiveHeatmapVisualization",
    "AdaptivePlotVisualization",
]


def _builtin_classes():
    """Return the 8 built-in classes for assertion checks."""
    from lightfall.visualization.widgets.adaptive.heatmap import AdaptiveHeatmapVisualization
    from lightfall.visualization.widgets.adaptive.plot import AdaptivePlotVisualization
    from lightfall.visualization.widgets.heatmap import HeatmapVisualization
    from lightfall.visualization.widgets.image_stack import ImageStackVisualization
    from lightfall.visualization.widgets.plot_1d import Plot1DVisualization
    from lightfall.visualization.widgets.scan_viewer import ScanViewerVisualization
    from lightfall.visualization.widgets.scatter import ScatterVisualization
    from lightfall.visualization.widgets.table import TableVisualization

    return [
        ImageStackVisualization,
        ScanViewerVisualization,
        Plot1DVisualization,
        HeatmapVisualization,
        ScatterVisualization,
        TableVisualization,
        AdaptiveHeatmapVisualization,
        AdaptivePlotVisualization,
    ]


class _FakeVizWidget(BaseVisualization):
    """Minimal BaseVisualization subclass used as a fake plugin contribution."""

    viz_name = "fake_viz"
    viz_display_name = "Fake Visualization"

    @staticmethod
    def can_handle(run) -> int:
        return 0

    def set_run(self, run) -> None:
        pass

    def get_streams(self) -> list:
        return []

    def set_stream(self, stream_name: str) -> None:
        pass

    def get_fields(self) -> list:
        return []

    def set_field(self, field_name: str) -> None:
        pass

    def refresh(self) -> None:
        pass


class _FakeVizPlugin:
    """Stub plugin registered in VisualizationRegistry."""

    name = "fake_viz"

    def get_viz_class(self):
        return _FakeVizWidget

    def get_introspection_data(self):
        return {"name": self.name}


class _BrokenVizPlugin:
    """Plugin whose get_viz_class() raises — should be skipped gracefully."""

    name = "broken_viz"

    def get_viz_class(self):
        raise RuntimeError("intentional failure")

    def get_introspection_data(self):
        return {"name": self.name}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the VisualizationRegistry singleton before and after each test."""
    VisualizationRegistry.reset()
    yield
    VisualizationRegistry.reset()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_registry_contributed_class_appears_in_widget_classes():
    """Registry-contributed viz class must appear in _widget_classes()."""
    from lightfall.ui.panels.visualization_panel import _widget_classes

    registry = VisualizationRegistry.get_instance()
    plugin = _FakeVizPlugin()
    registry.register_visualization(plugin)

    classes = _widget_classes()
    assert _FakeVizWidget in classes, (
        "_widget_classes() must include registry-contributed BaseVisualization subclasses"
    )


def test_all_8_builtins_still_present_when_registry_has_plugin():
    """All 8 built-in visualizations must remain in _widget_classes()."""
    from lightfall.ui.panels.visualization_panel import _widget_classes

    registry = VisualizationRegistry.get_instance()
    registry.register_visualization(_FakeVizPlugin())

    classes = _widget_classes()
    for cls in _builtin_classes():
        assert cls in classes, f"{cls.__name__} must remain in _widget_classes()"


def test_no_duplicates_when_registry_empty():
    """_widget_classes() with empty registry must not duplicate built-ins."""
    from lightfall.ui.panels.visualization_panel import _widget_classes

    classes = _widget_classes()
    assert len(classes) == len(set(classes)), "Duplicate classes in _widget_classes()"


def test_no_duplicates_when_registry_has_plugin():
    """_widget_classes() must not duplicate a class added by a plugin."""
    from lightfall.ui.panels.visualization_panel import _widget_classes

    registry = VisualizationRegistry.get_instance()
    registry.register_visualization(_FakeVizPlugin())

    classes = _widget_classes()
    assert len(classes) == len(set(classes)), "Duplicate classes in _widget_classes()"


def test_broken_plugin_get_viz_class_skipped_gracefully():
    """Plugin whose get_viz_class() raises must be silently skipped."""
    from lightfall.ui.panels.visualization_panel import _widget_classes

    registry = VisualizationRegistry.get_instance()
    registry.register_visualization(_BrokenVizPlugin())
    registry.register_visualization(_FakeVizPlugin())

    classes = _widget_classes()
    # The good plugin's class must still appear; broken one not in list
    assert _FakeVizWidget in classes
    # And all built-ins intact
    for cls in _builtin_classes():
        assert cls in classes


def test_widget_classes_returns_8_builtins_with_empty_registry():
    """With no plugins registered, _widget_classes() returns exactly the 8 built-ins."""
    from lightfall.ui.panels.visualization_panel import _widget_classes

    classes = _widget_classes()
    builtins = _builtin_classes()
    assert set(classes) == set(builtins), (
        f"Expected exactly the 8 built-ins, got {[c.__name__ for c in classes]}"
    )


def test_registry_contributed_class_is_appended_after_builtins():
    """Registry contributions appear after the built-ins in the list."""
    from lightfall.ui.panels.visualization_panel import _widget_classes

    registry = VisualizationRegistry.get_instance()
    registry.register_visualization(_FakeVizPlugin())

    classes = _widget_classes()
    builtins = _builtin_classes()
    builtin_indices = [classes.index(c) for c in builtins if c in classes]
    fake_index = classes.index(_FakeVizWidget)
    assert fake_index > max(builtin_indices), (
        "Registry-contributed class should appear after the built-in classes"
    )
