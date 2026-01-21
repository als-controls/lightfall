"""Panel system for NCS.

This package provides:
- BasePanel: Base class for all NCS panels with introspection
- PanelRegistry: Central registry for panel discovery and instantiation
- PanelMetadata: Metadata about panel types
- LogbookPanel: Default panel for experiment logbook
"""

from ncs.ui.panels.base import BasePanel, PanelMetadata
from ncs.ui.panels.logbook_panel import LogbookPanel
from ncs.ui.panels.registry import PanelRegistry

__all__ = [
    "BasePanel",
    "LogbookPanel",
    "PanelMetadata",
    "PanelRegistry",
]
