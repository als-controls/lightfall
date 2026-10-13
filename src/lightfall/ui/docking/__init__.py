"""Docking system using native QDockWidget.

This module provides VS Code/PyCharm-like docking with:
- Custom icon strip sidebar for panel navigation
- Left dock area: Primary tools (Bluesky, Devices) - one at a time
- Bottom dock area: Auxiliary panels (Claude, Documents, etc.) - one at a time
- Center area: Always-visible content (Logbook)
- Persistent layout state
- Theme integration (Islands mode with rounded panels)
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightfall.ui.docking.icon_sidebar import IconStripSidebar
    from lightfall.ui.docking.manager import DockingManager
    from lightfall.ui.docking.state import DockingState
    from lightfall.ui.docking.widget import PanelDockWidget

__all__ = [
    "DockingManager",
    "IconStripSidebar",
    "PanelDockWidget",
    "DockingState",
]

# Exports resolve lazily (PEP 562). lightfall.ui.theme imports the leaf
# lightfall.ui.docking.theme at import time; eager imports here would pull
# manager -> panels -> widgets.device_control -> ui.theme back in mid-init.
_EXPORTS = {
    "DockingManager": "manager",
    "IconStripSidebar": "icon_sidebar",
    "PanelDockWidget": "widget",
    "DockingState": "state",
}


def __getattr__(name: str):
    if name in _EXPORTS:
        value = getattr(importlib.import_module(f"{__name__}.{_EXPORTS[name]}"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
