"""Visualization widgets for Bluesky data display.

This package contains the concrete visualization widget implementations
built on the BaseVisualization ABC.

Widgets:
    - TableVisualization: Tabular view of all data fields
    - Plot1DVisualization: 1D line plots with optional curve fitting
    - HeatmapVisualization: 2D color maps for rectilinear data
    - ScatterVisualization: Scatter plots for irregular 2D data
    - ImageStackVisualization: Image sequence viewer with navigation
"""

from __future__ import annotations

from lightfall.visualization.widgets.heatmap import HeatmapVisualization
from lightfall.visualization.widgets.image_stack import ImageStackVisualization
from lightfall.visualization.widgets.plot_1d import Plot1DVisualization
from lightfall.visualization.widgets.scatter import ScatterVisualization
from lightfall.visualization.widgets.table import TableVisualization

__all__ = [
    "TableVisualization",
    "Plot1DVisualization",
    "HeatmapVisualization",
    "ScatterVisualization",
    "ImageStackVisualization",
]
