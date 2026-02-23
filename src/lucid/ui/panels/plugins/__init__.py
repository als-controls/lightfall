"""Panel plugin implementations for built-in NCS panels.

This module provides PanelPlugin wrappers for the built-in NCS panels,
allowing them to be loaded via the plugin system and registered with
PanelRegistry automatically.

Each plugin is a simple wrapper that returns the corresponding panel class.
Using the plugin system ensures panels are loaded at the correct time during
application startup (via preload=True in the manifest).
"""

from __future__ import annotations

from lucid.ui.panels.plugins.bluesky_plugin import BlueskyPanelPlugin
from lucid.ui.panels.plugins.device_plugin import DevicePanelPlugin
from lucid.ui.panels.plugins.documents_plugin import DocumentsPanelPlugin
from lucid.ui.panels.plugins.logbook_entries_plugin import LogbookEntriesPanelPlugin
from lucid.ui.panels.plugins.logbook_plugin import LogbookPanelPlugin

__all__ = [
    "BlueskyPanelPlugin",
    "DevicePanelPlugin",
    "DocumentsPanelPlugin",
    "LogbookEntriesPanelPlugin",
    "LogbookPanelPlugin",
]

# Claude panel (built into LUCID)
try:
    from lucid.ui.panels.plugins.claude_plugin import ClaudePanelPlugin  # noqa: F401

    __all__.append("ClaudePanelPlugin")
except ImportError:
    pass
