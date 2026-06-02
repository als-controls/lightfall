"""Docking system using native QDockWidget.

This module provides VS Code/PyCharm-like docking with:
- Custom icon strip sidebar for panel navigation
- Left dock area: Primary tools (Bluesky, Devices) - one at a time
- Bottom dock area: Auxiliary panels (Claude, Documents, etc.) - one at a time
- Center area: Always-visible content (Logbook)
- Persistent layout state
- Theme integration (Islands mode with rounded panels)
"""

from lucid.ui.docking.icon_sidebar import IconStripSidebar
from lucid.ui.docking.manager import DockingManager
from lucid.ui.docking.state import DockingState
from lucid.ui.docking.widget import PanelDockWidget

__all__ = [
    "DockingManager",
    "IconStripSidebar",
    "PanelDockWidget",
    "DockingState",
]
