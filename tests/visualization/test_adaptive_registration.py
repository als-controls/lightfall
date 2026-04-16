"""Test that adaptive widgets are registered in _widget_classes()."""

from __future__ import annotations


def test_adaptive_heatmap_registered():
    from lucid.ui.panels.visualization_panel import _widget_classes
    from lucid.visualization.widgets.adaptive.heatmap import (
        AdaptiveHeatmapVisualization,
    )

    classes = _widget_classes()
    assert AdaptiveHeatmapVisualization in classes


def test_adaptive_plot_registered():
    from lucid.ui.panels.visualization_panel import _widget_classes
    from lucid.visualization.widgets.adaptive.plot import (
        AdaptivePlotVisualization,
    )

    classes = _widget_classes()
    assert AdaptivePlotVisualization in classes
