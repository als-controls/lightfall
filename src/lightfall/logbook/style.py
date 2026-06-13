"""
Theme-aware styling utilities for logbook widgets.

Provides colors and stylesheets that adapt to the system's light/dark theme.
"""

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication


def scaled_pt(ref_pt: float) -> int:
    """Scale a logbook font point size to the Appearance base font size.

    ``ref_pt`` is the design size at the reference 10pt base, so logbook text
    stays proportional to the Appearance > Font Size setting. Read at the time
    a widget/stylesheet is built, so callers pick up changes on the next
    (re)render.

    Args:
        ref_pt: The point size at the reference 10pt base.

    Returns:
        The scaled point size.
    """
    from lightfall.ui.theme import ThemeManager

    return ThemeManager.get_instance().scale_pt(ref_pt)


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


def get_protected_background_color() -> str:
    """
    Get the background color for protected content regions.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#2d2d2d"  # Dark gray
    else:
        return "#f0f0f0"  # Light gray


def get_protected_border_color() -> str:
    """
    Get the border color for protected content regions.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#555555"  # Medium gray
    else:
        return "#b0b0b0"  # Medium-light gray


def get_protected_text_color() -> str:
    """
    Get the text color for protected content regions.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#888888"  # Medium gray
    else:
        return "#707070"  # Dark gray


def get_header_color() -> str:
    """
    Get the color for markdown headers.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#82aaff"  # Light blue
    else:
        return "#0066cc"  # Medium blue


def get_link_color() -> str:
    """
    Get the color for links.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#80cbc4"  # Teal
    else:
        return "#0077aa"  # Dark cyan


def get_code_background_color() -> str:
    """
    Get the background color for code blocks.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#2d2d2d"  # Dark gray
    else:
        return "#f5f5f5"  # Light gray


def get_code_text_color() -> str:
    """
    Get the text color for code blocks.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#e06c75"  # Soft red
    else:
        return "#c7254e"  # Dark red


def get_blockquote_color() -> str:
    """
    Get the color for blockquote borders.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#5c6370"  # Gray
    else:
        return "#dfe2e5"  # Light gray


def get_emphasis_color() -> str:
    """
    Get the color for emphasized (italic) text highlighting.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#c678dd"  # Purple
    else:
        return "#6f42c1"  # Dark purple


def get_strong_color() -> str:
    """
    Get the color for strong (bold) text highlighting.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#e5c07b"  # Yellow
    else:
        return "#e83e8c"  # Pink


def get_marker_color() -> str:
    """
    Get the color for markdown markers (*, #, -, etc.).

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#5c6370"  # Muted gray
    else:
        return "#6c757d"  # Bootstrap gray


def get_action_group_background_color() -> str:
    """
    Get the background color for action group entries.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#1e3a5f"  # Dark blue
    else:
        return "#e3f2fd"  # Light blue


def get_action_group_border_color() -> str:
    """
    Get the border color for action group entries.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#2196f3"  # Blue
    else:
        return "#1976d2"  # Darker blue


def get_action_group_icon_color() -> str:
    """
    Get the color for action group expand/collapse icon.

    Returns:
        CSS color string appropriate for the current theme.
    """
    if is_dark_theme():
        return "#64b5f6"  # Light blue
    else:
        return "#1565c0"  # Dark blue


class LogbookStyles:
    """
    Centralized style definitions for logbook widgets.

    Use these methods to get consistent, theme-aware stylesheets.
    """

    @staticmethod
    def editor_base() -> str:
        """Base stylesheet for editor widgets."""
        return f"""
            QPlainTextEdit, QTextEdit {{
                font-family: 'Cascadia Code', 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: {scaled_pt(10)}pt;
                border: 1px solid palette(mid);
                border-radius: 4px;
            }}
        """

    @staticmethod
    def protected_highlight() -> str:
        """Stylesheet for protected content highlighting (for QSS)."""
        bg = get_protected_background_color()
        border = get_protected_border_color()
        return f"""
            background-color: {bg};
            border-left: 3px solid {border};
        """

    @staticmethod
    def toolbar() -> str:
        """Stylesheet for the logbook toolbar."""
        return """
            QToolBar {
                spacing: 4px;
                padding: 4px;
                border: none;
                border-bottom: 1px solid palette(mid);
            }
            QToolButton {
                padding: 4px 8px;
                border-radius: 3px;
            }
            QToolButton:checked {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
            QToolButton:hover:!checked {
                background-color: palette(midlight);
            }
        """


def get_qt_html_stylesheet() -> str:
    """
    Get a CSS stylesheet for use in QTextEdit HTML content.

    This stylesheet provides styling for the HTML rendered in the
    WYSIWYG editor.

    Returns:
        CSS stylesheet string.
    """
    header_color = get_header_color()
    link_color = get_link_color()
    code_bg = get_code_background_color()
    code_text = get_code_text_color()
    blockquote_border = get_blockquote_color()
    protected_bg = get_protected_background_color()
    protected_border = get_protected_border_color()
    protected_text = get_protected_text_color()
    action_bg = get_action_group_background_color()
    action_border = get_action_group_border_color()
    action_icon = get_action_group_icon_color()

    return f"""
        h1, h2, h3, h4, h5, h6 {{
            color: {header_color};
            margin-top: 16px;
            margin-bottom: 8px;
        }}
        h1 {{ font-size: {scaled_pt(24)}pt; }}
        h2 {{ font-size: {scaled_pt(20)}pt; }}
        h3 {{ font-size: {scaled_pt(16)}pt; }}
        h4 {{ font-size: {scaled_pt(14)}pt; }}
        h5 {{ font-size: {scaled_pt(12)}pt; }}
        h6 {{ font-size: {scaled_pt(10)}pt; }}

        a {{
            color: {link_color};
            text-decoration: underline;
        }}

        code {{
            font-family: 'Cascadia Code', 'Consolas', monospace;
            background-color: {code_bg};
            color: {code_text};
            padding: 2px 4px;
            border-radius: 3px;
        }}

        pre {{
            font-family: 'Cascadia Code', 'Consolas', monospace;
            background-color: {code_bg};
            padding: 8px;
            border-radius: 4px;
            overflow-x: auto;
        }}

        blockquote {{
            border-left: 4px solid {blockquote_border};
            margin-left: 0;
            padding-left: 16px;
            color: inherit;
            opacity: 0.85;
        }}

        table {{
            border-collapse: collapse;
            margin: 8px 0;
        }}

        th, td {{
            border: 1px solid palette(mid);
            padding: 6px 12px;
        }}

        th {{
            background-color: palette(midlight);
            font-weight: bold;
        }}

        .protected {{
            background-color: {protected_bg};
            border-left: 3px solid {protected_border};
            padding-left: 8px;
            color: {protected_text};
        }}

        .system-entry {{
            background-color: {action_bg};
            border-left: 3px solid {action_border};
            padding: 4px 8px;
            margin: 2px 0;
            display: block;
        }}

        .system-entry-summary {{
            background-color: {action_bg};
            border-left: 3px solid {action_border};
            padding: 8px 12px;
            margin: 4px 0;
            cursor: pointer;
        }}

        .expand-icon {{
            color: {action_icon};
            font-family: monospace;
            font-weight: bold;
            margin-right: 8px;
        }}

        /* Action group links - style clickable action summaries */
        a[href^="ncs://action/"] {{
            color: {action_icon};
            background-color: {action_bg};
            padding: 4px 8px;
            border-radius: 4px;
            border-left: 3px solid {action_border};
            text-decoration: none;
            display: inline-block;
            margin: 4px 0;
        }}

        a[href^="ncs://action/"]:hover {{
            text-decoration: underline;
        }}
    """
