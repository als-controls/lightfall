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

from ncs.ui.panels.base import BasePanel, PanelMetadata
from ncs.ui.panels.bluesky_panel import BlueskyPanel
from ncs.ui.panels.device_panel import DevicePanel
from ncs.ui.panels.documents_panel import DocumentsPanel
from ncs.ui.panels.logbook_panel import LogbookPanel
from ncs.ui.panels.registry import PanelRegistry

__all__ = [
    "BasePanel",
    "BlueskyPanel",
    "DevicePanel",
    "DocumentsPanel",
    "LogbookPanel",
    "PanelMetadata",
    "PanelRegistry",
]
