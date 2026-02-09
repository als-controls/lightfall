"""Visualization widgets for Bluesky data display.

This package contains the concrete visualization widget implementations
and their corresponding plugins.

Widgets:
    - TableVisualization: Tabular view of all data fields
    - PlotVisualization: 1D line plots with optional curve fitting
    - HeatmapVisualization: 2D color maps for rectilinear data
    - ScatterVisualization: Scatter plots for irregular 2D data
    - ImageStackVisualization: Image sequence viewer with navigation
    - VolumeVisualization: 3D volume viewer with slice selection
"""

from __future__ import annotations

from lucid.visualization.widgets.heatmap import (
    HeatmapVisualization,
    HeatmapVisualizationPlugin,
)
from lucid.visualization.widgets.image_sequence import (
    ImageStackVisualization,
    ImageStackVisualizationPlugin,
)
from lucid.visualization.widgets.plot import PlotVisualization, PlotVisualizationPlugin
from lucid.visualization.widgets.scatter import (
    ScatterVisualization,
    ScatterVisualizationPlugin,
)
from lucid.visualization.widgets.table import (
    TableVisualization,
    TableVisualizationPlugin,
)
from lucid.visualization.widgets.volume import (
    VolumeVisualization,
    VolumeVisualizationPlugin,
)

__all__ = [
    "TableVisualization",
    "TableVisualizationPlugin",
    "PlotVisualization",
    "PlotVisualizationPlugin",
    "HeatmapVisualization",
    "HeatmapVisualizationPlugin",
    "ScatterVisualization",
    "ScatterVisualizationPlugin",
    "ImageStackVisualization",
    "ImageStackVisualizationPlugin",
    "VolumeVisualization",
    "VolumeVisualizationPlugin",
]
