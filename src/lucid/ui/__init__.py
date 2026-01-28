"""User interface components for NCS.

This package provides:
- Main window and common UI components
- Theme management
- Panel system with progressive disclosure
- Preferences management
- Toast notifications
"""

from lucid.ui.mainwindow import NCSMainWindow
from lucid.ui.toast import ToastManager

__all__ = [
    "NCSMainWindow",
    "ToastManager",
]
