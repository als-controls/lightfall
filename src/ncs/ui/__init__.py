"""User interface components for NCS.

This package provides:
- Main window and common UI components
- Theme management
- Panel system with progressive disclosure
- Preferences management
- Toast notifications
"""

from ncs.ui.mainwindow import NCSMainWindow
from ncs.ui.toast import ToastManager

__all__ = [
    "NCSMainWindow",
    "ToastManager",
]
