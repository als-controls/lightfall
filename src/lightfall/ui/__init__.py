"""User interface components for LUCID.

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
        from lucid.ui.dialogs import LoginDialog
        return LoginDialog
    if name == "NCSMainWindow":
        from lucid.ui.mainwindow import NCSMainWindow
        return NCSMainWindow
    if name == "ToastManager":
        from lucid.ui.toast import ToastManager
        return ToastManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "LoginDialog",
    "NCSMainWindow",
    "ToastManager",
]
