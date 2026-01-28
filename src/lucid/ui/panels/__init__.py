"""Panel system for NCS.

This package provides:
- BasePanel: Base class for all NCS panels with introspection
- PanelRegistry: Central registry for panel discovery and instantiation
- PanelMetadata: Metadata about panel types
- LogbookPanel: Default panel for experiment logbook
- DevicePanel: Panel for device management
- BlueskyPanel: Panel for Bluesky plan execution
- DocumentsPanel: Panel for viewing Bluesky document streams
"""

from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.panels.bluesky_panel import BlueskyPanel
from lucid.ui.panels.device_panel import DevicePanel
from lucid.ui.panels.documents_panel import DocumentsPanel
from lucid.ui.panels.logbook_panel import LogbookPanel
from lucid.ui.panels.registry import PanelRegistry

__all__ = [
    "BasePanel",
    "BlueskyPanel",
    "DevicePanel",
    "DocumentsPanel",
    "LogbookPanel",
    "PanelMetadata",
    "PanelRegistry",
]
