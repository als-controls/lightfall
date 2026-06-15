"""Scan Viewer must be registered with the visualization panel."""
from __future__ import annotations

from lightfall.ui.panels.visualization_panel import _widget_classes


def test_scan_viewer_is_registered():
    names = [c.viz_name for c in _widget_classes()]
    assert "scan_viewer" in names
