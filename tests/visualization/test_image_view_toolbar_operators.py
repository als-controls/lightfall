"""The ROI stat combo must expose the frame-series variation operators."""
from __future__ import annotations

from lightfall.visualization.widgets.image_stack import ImageStackVisualization


def test_roi_stat_combo_includes_variation_operators(qtbot):
    w = ImageStackVisualization()
    qtbot.addWidget(w)
    items = [w._roi_stat_combo.itemText(i) for i in range(w._roi_stat_combo.count())]
    # existing basic stats preserved
    assert "Mean" in items and "Std" in items
    # new variation operators added
    assert "Chi2 (consecutive)" in items
    assert "Norm Abs Derivative" in items
