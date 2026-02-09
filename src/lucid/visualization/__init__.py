"""Live data visualization system for Bluesky document streams.

This package provides a heuristic-driven live visualization system that
automatically analyzes incoming data characteristics and selects appropriate
visualizations.

Main Components:
    - DataCharacteristics: Describes data shape, types, and gridding
    - VisualizationSpec: Configuration for a specific visualization
    - SelectionEngine: Selects best visualization for given data
    - DocumentProcessor: Extracts characteristics from Bluesky documents
    - BaseVisualizationWidget: Base class for all visualization widgets

Visualization Types:
    - TABLE: Tabular display of all data fields
    - PLOT_1D: Line plots for 1D scans
    - HEATMAP: 2D rectilinear data as color maps
    - SCATTER: 2D irregular data as scatter plots
    - IMAGE_STACK: Sequences of 2D images
    - VOLUME: 3D volumetric data with slice navigation

Example:
    >>> from lucid.visualization import SelectionEngine, DocumentProcessor
    >>> from lucid.acquire.buffer import MultiStreamBuffer
    >>>
    >>> buffer = MultiStreamBuffer()
    >>> processor = DocumentProcessor()
    >>> engine = SelectionEngine()
    >>>
    >>> # On start document, extract characteristics
    >>> processor("start", start_doc)
    >>> processor("descriptor", descriptor_doc)
    >>> characteristics = processor.get_characteristics()
    >>>
    >>> # Select best visualization
    >>> results = engine.select_visualizations(characteristics)
    >>> plugin, score = results[0]
    >>> widget = plugin.create_widget(spec, buffer)
"""

from __future__ import annotations

from lucid.visualization.base import BaseVisualizationWidget
from lucid.visualization.memory import (
    StreamingDecimator,
    auto_decimate,
    decimate_lttb,
    decimate_minmax,
)
from lucid.visualization.processor import DocumentProcessor
from lucid.visualization.registry import VisualizationRegistry
from lucid.visualization.selection import SelectionEngine
from lucid.visualization.spec import (
    DataCharacteristics,
    FieldInfo,
    FieldType,
    VisualizationSpec,
    VizType,
)
from lucid.visualization.theme import (
    ThemedVisualizationMixin,
    VisualizationColors,
    apply_pyqtgraph_theme,
    get_visualization_colors,
)

__all__ = [
    # Core data structures
    "DataCharacteristics",
    "FieldInfo",
    "FieldType",
    "VizType",
    "VisualizationSpec",
    # Engine components
    "DocumentProcessor",
    "SelectionEngine",
    "VisualizationRegistry",
    # Base widget
    "BaseVisualizationWidget",
    # Theme
    "ThemedVisualizationMixin",
    "VisualizationColors",
    "apply_pyqtgraph_theme",
    "get_visualization_colors",
    # Memory/decimation
    "StreamingDecimator",
    "auto_decimate",
    "decimate_lttb",
    "decimate_minmax",
]
