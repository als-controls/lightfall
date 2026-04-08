"""
Theme-aware styling utilities for EPICS widgets.

Provides colors that adapt to the system's light/dark theme.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def is_dark_theme() -> bool:
    """
    Detect if the application is using a dark theme.

    Returns:
        True if the current theme appears to be dark.
    """
    app = QApplication.instance()
    if app is None:
        return False

    palette = app.palette()
    window_color = palette.color(QPalette.ColorRole.Window)
    # Consider it dark if the window background luminance is low
    luminance = (
        0.299 * window_color.redF()
        + 0.587 * window_color.greenF()
        + 0.114 * window_color.blueF()
    )
    return luminance < 0.5


def get_disconnected_color() -> str:
    """
    Get the background color for disconnected state.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#5c2020"  # Dark muted red
    else:
        return "#ffcccc"  # Light pink-red


def get_modified_color() -> str:
    """
    Get the background color for modified/pending state.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#4a4a20"  # Dark muted yellow
    else:
        return "#ffffcc"  # Light yellow


def get_error_color() -> str:
    """
    Get the background color for error state.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#5c2020"  # Dark muted red
    else:
        return "#ffcccc"  # Light pink-red


def get_warning_color() -> str:
    """
    Get the color for warning state.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#b8860b"  # Dark goldenrod
    else:
        return "#ffc107"  # Amber/yellow


def get_success_color() -> str:
    """
    Get the background color for success state.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#1e3d1e"  # Dark muted green
    else:
        return "#d4edda"  # Light green


def get_info_color() -> str:
    """
    Get the background color for informational state.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#1e3d4a"  # Dark muted blue
    else:
        return "#cce5ff"  # Light blue


def get_text_color() -> str:
    """
    Get the primary text color for the current theme.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#e0e0e0"
    else:
        return "#212529"


def get_muted_text_color() -> str:
    """
    Get the muted/secondary text color for the current theme.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#a0a0a0"
    else:
        return "#6c757d"


def get_code_background_color() -> str:
    """
    Get the background color for code/monospace blocks.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#2d2d2d"
    else:
        return "#f0f0f0"


class WidgetStyles:
    """
    Centralized style definitions for EPICS widgets.

    Use these methods to get consistent, theme-aware stylesheets.
    """

    @staticmethod
    def disconnected() -> str:
        """Stylesheet for disconnected PV state."""
        bg = get_disconnected_color()
        return f"background-color: {bg};"

    @staticmethod
    def connected() -> str:
        """Stylesheet for connected PV state (default/clear)."""
        return ""

    @staticmethod
    def modified() -> str:
        """Stylesheet for modified/pending input state."""
        bg = get_modified_color()
        return f"background-color: {bg};"

    @staticmethod
    def error() -> str:
        """Stylesheet for error state."""
        bg = get_error_color()
        return f"background-color: {bg};"

    @staticmethod
    def readonly() -> str:
        """Stylesheet for read-only widgets."""
        if is_dark_theme():
            return "background-color: #3a3a3a;"
        else:
            return "background-color: #f0f0f0;"
