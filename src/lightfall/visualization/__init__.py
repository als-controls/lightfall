"""Live data visualization system for Bluesky document streams.

Main Components:
    - BaseVisualization: ABC for all tiled-backed visualization widgets
    - FieldType / FieldInfo: Data field classification
    - VisualizationRegistry: Registry for visualization plugins
"""

from __future__ import annotations

from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.registry import VisualizationRegistry
from lightfall.visualization.spec import (
    FieldInfo,
    FieldType,
    VizType,
)
from lightfall.visualization.theme import (
    VisualizationColors,
    apply_pyqtgraph_theme,
    get_visualization_colors,
)

__all__ = [
    # Base widget
    "BaseVisualization",
    # Core data structures
    "FieldInfo",
    "FieldType",
    "VizType",
    # Registry
    "VisualizationRegistry",
    # Theme
    "VisualizationColors",
    "apply_pyqtgraph_theme",
    "get_visualization_colors",
]
