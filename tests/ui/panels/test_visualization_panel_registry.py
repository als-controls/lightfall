"""VisualizationPanel must surface plugin-contributed visualizations.

Regression: plugins register a viz with VisualizationRegistry, but the panel
built its class list from a hardcoded _widget_classes() and never consulted the
registry, so plugin viz never appeared in the dropdown or auto-selection.
"""
from __future__ import annotations

import pytest

from lightfall.plugins.visualization_plugin import VisualizationPlugin
from lightfall.ui.panels.visualization_panel import _widget_classes
from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.registry import VisualizationRegistry


class _RegViz(BaseVisualization):
    viz_name = "reg_viz"
    viz_display_name = "Registry Viz"

    @staticmethod
    def can_handle(run) -> int:
        return 0


class _RegVizPlugin(VisualizationPlugin):
    @property
    def name(self) -> str:
        return "reg_viz"

    def get_viz_class(self):
        return _RegViz


@pytest.fixture(autouse=True)
def reset_viz_registry():
    VisualizationRegistry.reset()
    yield
    VisualizationRegistry.reset()


def test_widget_classes_includes_registry_contributed_viz(qtbot):
    """A viz plugin registered in the registry appears in _widget_classes()."""
    VisualizationRegistry.get_instance().register_visualization(_RegVizPlugin())

    classes = _widget_classes()

    assert _RegViz in classes


def test_widget_classes_still_includes_builtins(qtbot):
    """The fix must not drop the built-in visualizations."""
    from lightfall.visualization.widgets.plot_1d import Plot1DVisualization

    assert Plot1DVisualization in _widget_classes()
