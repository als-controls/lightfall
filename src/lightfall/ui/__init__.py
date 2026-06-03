"""User interface components for Lightfall.

This package provides:
- Main window and common UI components
- Theme management
- Panel system with progressive disclosure
- Preferences management
- Toast notifications
- Login dialog
"""

def __getattr__(name):
    if name == "LoginDialog":
        from lightfall.ui.dialogs import LoginDialog
        return LoginDialog
    if name == "LFMainWindow":
        from lightfall.ui.mainwindow import LFMainWindow
        return LFMainWindow
    if name == "ToastManager":
        from lightfall.ui.toast import ToastManager
        return ToastManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "LoginDialog",
    "LFMainWindow",
    "ToastManager",
]
