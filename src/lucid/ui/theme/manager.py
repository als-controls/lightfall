"""Theme manager for NCS application theming.

Provides application-wide theme control with support for:
- Light, dark, and system-following modes
- Beamline-specific color accents
- Theme-aware color utilities
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class Theme(Enum):
    """Available theme modes."""

    LIGHT = "light"
    DARK = "dark"  # Alias for default dark theme (Slate)
    SLATE = "slate"  # Neutral gray dark theme
    DARKBLUE = "darkblue"  # Blue-gray dark theme
    SYSTEM = "system"  # Follow system preference


@dataclass
class ThemeColors:
    """Color definitions for a theme.

    Attributes:
        primary: Primary brand/accent color.
        secondary: Secondary accent color.
        success: Success/positive state color.
        warning: Warning state color.
        error: Error/danger state color.
        info: Informational state color.
        background: Main background color.
        surface: Elevated surface color.
        text: Primary text color.
        text_secondary: Secondary/muted text color.
        border: Border/divider color.
    """

    primary: str = "#2563eb"  # Blue
    secondary: str = "#7c3aed"  # Purple
    success: str = "#16a34a"  # Green
    warning: str = "#d97706"  # Amber
    error: str = "#dc2626"  # Red
    info: str = "#0891b2"  # Cyan

    background: str = "#ffffff"
    surface: str = "#f3f4f6"
    text: str = "#1f2937"
    text_secondary: str = "#6b7280"
    border: str = "#e5e7eb"

    # Connection state colors
    connected: str = ""
    disconnected: str = ""

    def __post_init__(self) -> None:
        """Set default state colors based on theme."""
        if not self.connected:
            self.connected = self.success
        if not self.disconnected:
            self.disconnected = self.error


# Pre-defined theme color schemes
LIGHT_COLORS = ThemeColors(
    primary="#2563eb",
    secondary="#7c3aed",
    success="#16a34a",
    warning="#d97706",
    error="#dc2626",
    info="#0891b2",
    background="#ffffff",
    surface="#f3f4f6",
    text="#1f2937",
    text_secondary="#6b7280",
    border="#e5e7eb",
    disconnected="#ffcccc",
)

SLATE_COLORS = ThemeColors(
    primary="#3b82f6",
    secondary="#8b5cf6",
    success="#22c55e",
    warning="#f59e0b",
    error="#ef4444",
    info="#06b6d4",
    background="#1e1e1e",
    surface="#2d2d2d",
    text="#d4d4d4",
    text_secondary="#808080",
    border="#3e3e3e",
    disconnected="#5c2020",
)

DARKBLUE_COLORS = ThemeColors(
    primary="#3b82f6",
    secondary="#8b5cf6",
    success="#22c55e",
    warning="#f59e0b",
    error="#ef4444",
    info="#06b6d4",
    background="#1f2937",
    surface="#374151",
    text="#f3f4f6",
    text_secondary="#9ca3af",
    border="#4b5563",
    disconnected="#5c2020",
)


@dataclass
class BeamlineTheme:
    """Beamline-specific theme customizations.

    Attributes:
        name: Beamline identifier.
        display_name: Human-readable beamline name.
        accent_color: Custom accent/primary color.
        logo_path: Path to beamline logo.
        custom_colors: Additional custom color overrides.
    """

    name: str
    display_name: str = ""
    accent_color: str | None = None
    logo_path: str | None = None
    custom_colors: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.name


class ThemeManager(QObject):
    """
    Application-wide theme manager.

    ThemeManager provides:
    - Theme mode switching (light/dark/system)
    - System theme detection and following
    - Beamline-specific customization
    - Theme-aware color utilities
    - Stylesheet generation

    Signals:
        theme_changed: Emitted when theme mode changes.
        colors_changed: Emitted when colors are updated.

    Example:
        >>> manager = ThemeManager.get_instance()
        >>> manager.set_theme(Theme.DARK)
        >>> manager.colors.background
        '#1f2937'
    """

    theme_changed = Signal(Theme)
    colors_changed = Signal()

    _instance: ThemeManager | None = None
    _lock = threading.RLock()

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the theme manager."""
        super().__init__(parent)
        self._theme = Theme.SYSTEM
        self._effective_theme = Theme.LIGHT
        self._colors = LIGHT_COLORS
        self._beamline_theme: BeamlineTheme | None = None
        self._custom_stylesheets: dict[str, str] = {}

        # Detect system theme
        self._update_effective_theme()

    @classmethod
    def get_instance(cls) -> ThemeManager:
        """Get the singleton ThemeManager instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.deleteLater()
            cls._instance = None

    @property
    def theme(self) -> Theme:
        """Current theme mode setting."""
        return self._theme

    @property
    def effective_theme(self) -> Theme:
        """Actual theme being used (resolves SYSTEM to LIGHT/DARK)."""
        return self._effective_theme

    @property
    def is_dark(self) -> bool:
        """Whether the effective theme is dark."""
        return self._effective_theme in (Theme.DARK, Theme.SLATE, Theme.DARKBLUE)

    @property
    def colors(self) -> ThemeColors:
        """Current theme colors."""
        return self._colors

    @property
    def beamline_theme(self) -> BeamlineTheme | None:
        """Current beamline-specific theme."""
        return self._beamline_theme

    def set_theme(self, theme: Theme) -> None:
        """Set the theme mode.

        Args:
            theme: The theme mode to use.
        """
        if theme == self._theme:
            return

        old_theme = self._theme
        self._theme = theme
        self._update_effective_theme()

        logger.info("Theme changed: {} -> {}", old_theme.value, theme.value)
        self.theme_changed.emit(theme)

    def _update_effective_theme(self) -> None:
        """Update the effective theme based on current settings."""
        if self._theme == Theme.SYSTEM:
            self._effective_theme = self._detect_system_theme()
        else:
            self._effective_theme = self._theme

        # Update colors based on theme
        theme_colors = {
            Theme.LIGHT: LIGHT_COLORS,
            Theme.DARK: SLATE_COLORS,  # DARK aliases to Slate
            Theme.SLATE: SLATE_COLORS,
            Theme.DARKBLUE: DARKBLUE_COLORS,
        }
        base_colors = theme_colors.get(self._effective_theme, LIGHT_COLORS)
        self._colors = ThemeColors(**vars(base_colors))

        # Apply beamline customizations
        if self._beamline_theme:
            self._apply_beamline_colors()

        self.colors_changed.emit()

    def _detect_system_theme(self) -> Theme:
        """Detect if the system is using dark mode."""
        app = QApplication.instance()
        if app is None:
            return Theme.LIGHT

        palette = app.palette()
        window_color = palette.color(QPalette.ColorRole.Window)

        # Calculate luminance
        luminance = (
            0.299 * window_color.redF()
            + 0.587 * window_color.greenF()
            + 0.114 * window_color.blueF()
        )

        return Theme.DARK if luminance < 0.5 else Theme.LIGHT

    def set_beamline_theme(self, theme: BeamlineTheme | None) -> None:
        """Set beamline-specific theme customizations.

        Args:
            theme: Beamline theme or None to clear.
        """
        self._beamline_theme = theme
        self._update_effective_theme()

        if theme:
            logger.info("Applied beamline theme: {}", theme.name)

    def _apply_beamline_colors(self) -> None:
        """Apply beamline color overrides to current colors."""
        if not self._beamline_theme:
            return

        # Apply accent color as primary
        if self._beamline_theme.accent_color:
            self._colors.primary = self._beamline_theme.accent_color

        # Apply any custom color overrides
        for key, value in self._beamline_theme.custom_colors.items():
            if hasattr(self._colors, key):
                setattr(self._colors, key, value)

    def apply_to_application(self) -> None:
        """Apply the current theme to the Qt application."""
        app = QApplication.instance()
        if app is None:
            return

        # Set application palette
        if self.is_dark:
            self._apply_dark_palette(app)
        else:
            self._apply_light_palette(app)

        # Apply global stylesheet
        stylesheet = self.generate_stylesheet()
        app.setStyleSheet(stylesheet)

        logger.debug("Applied {} theme to application", self._effective_theme.value)

    def _apply_dark_palette(self, app: QApplication) -> None:
        """Apply a dark color palette to the application."""
        palette = QPalette()

        # Window colors
        palette.setColor(QPalette.ColorRole.Window, QColor(self._colors.background))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(self._colors.text))
        palette.setColor(QPalette.ColorRole.Base, QColor(self._colors.surface))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(self._colors.background))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(self._colors.surface))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(self._colors.text))

        # Text colors
        palette.setColor(QPalette.ColorRole.Text, QColor(self._colors.text))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(self._colors.text_secondary))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))

        # Button colors
        palette.setColor(QPalette.ColorRole.Button, QColor(self._colors.surface))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(self._colors.text))

        # Selection colors
        palette.setColor(QPalette.ColorRole.Highlight, QColor(self._colors.primary))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))

        # Links
        palette.setColor(QPalette.ColorRole.Link, QColor(self._colors.primary))
        palette.setColor(QPalette.ColorRole.LinkVisited, QColor(self._colors.secondary))

        app.setPalette(palette)

    def _apply_light_palette(self, app: QApplication) -> None:
        """Apply a light color palette to the application."""
        palette = QPalette()

        # Window colors
        palette.setColor(QPalette.ColorRole.Window, QColor(self._colors.background))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(self._colors.text))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(self._colors.surface))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(self._colors.text))

        # Text colors
        palette.setColor(QPalette.ColorRole.Text, QColor(self._colors.text))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(self._colors.text_secondary))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))

        # Button colors
        palette.setColor(QPalette.ColorRole.Button, QColor(self._colors.surface))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(self._colors.text))

        # Selection colors
        palette.setColor(QPalette.ColorRole.Highlight, QColor(self._colors.primary))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))

        # Links
        palette.setColor(QPalette.ColorRole.Link, QColor(self._colors.primary))
        palette.setColor(QPalette.ColorRole.LinkVisited, QColor(self._colors.secondary))

        app.setPalette(palette)

    def generate_stylesheet(self) -> str:
        """Generate a global stylesheet for the current theme.

        Returns:
            CSS stylesheet string.
        """
        c = self._colors
        return f"""
/* NCS Global Theme Stylesheet */

/* Scrollbars */
QScrollBar:vertical {{
    background: {c.surface};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c.border};
    min-height: 30px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c.text_secondary};
}}
QScrollBar:horizontal {{
    background: {c.surface};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {c.border};
    min-width: 30px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c.text_secondary};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    border: none;
    background: none;
}}

/* Tool tips */
QToolTip {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    padding: 4px;
}}

/* Menu */
QMenu {{
    background-color: {c.background};
    border: 1px solid {c.border};
}}
QMenu::item {{
    padding: 6px 24px;
}}
QMenu::item:selected {{
    background-color: {c.primary};
    color: white;
}}
QMenu::separator {{
    height: 1px;
    background: {c.border};
    margin: 4px 8px;
}}

/* Tab widget */
QTabWidget::pane {{
    border: 1px solid {c.border};
    background: {c.background};
}}
QTabBar::tab {{
    background: {c.surface};
    border: 1px solid {c.border};
    padding: 8px 16px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {c.background};
    border-bottom-color: {c.background};
}}

/* Dock widgets */
QDockWidget {{
    titlebar-close-icon: url(close.png);
    titlebar-normal-icon: url(float.png);
}}
QDockWidget::title {{
    background: {c.surface};
    padding: 6px;
}}

/* Status bar */
QStatusBar {{
    background: {c.surface};
    border-top: 1px solid {c.border};
}}

/* Group box */
QGroupBox {{
    font-weight: bold;
    border: 1px solid {c.border};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}

/* Line edit */
QLineEdit {{
    border: 1px solid {c.border};
    border-radius: 4px;
    padding: 4px 8px;
    background: {c.background};
}}
QLineEdit:focus {{
    border-color: {c.primary};
}}

/* Combo box */
QComboBox {{
    border: 1px solid {c.border};
    border-radius: 4px;
    padding: 4px 8px;
    background: {c.background};
}}
QComboBox:focus {{
    border-color: {c.primary};
}}

/* Spin box */
QSpinBox, QDoubleSpinBox {{
    border: 1px solid {c.border};
    border-radius: 4px;
    padding: 4px;
    background: {c.background};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {c.primary};
}}

/* Push button */
QPushButton {{
    background: {c.surface};
    border: 1px solid {c.border};
    border-radius: 4px;
    padding: 6px 16px;
}}
QPushButton:hover {{
    background: {c.border};
}}
QPushButton:pressed {{
    background: {c.text_secondary};
}}
QPushButton:disabled {{
    background: {c.surface};
    color: {c.text_secondary};
}}

/* Primary button */
QPushButton[primary="true"] {{
    background: {c.primary};
    color: white;
    border: none;
}}
QPushButton[primary="true"]:hover {{
    background: {self._adjust_color(c.primary, -20)};
}}

/* Progress bar */
QProgressBar {{
    border: 1px solid {c.border};
    border-radius: 4px;
    text-align: center;
    background: {c.surface};
}}
QProgressBar::chunk {{
    background: {c.primary};
    border-radius: 3px;
}}

/* Splitter */
QSplitter::handle {{
    background: {c.border};
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}

/* Tree and list views */
QTreeView, QListView, QTableView {{
    border: 1px solid {c.border};
}}
QTreeView::item, QListView::item, QTableView::item {{
    background: {c.background};
}}
QTreeView::item:alternate, QListView::item:alternate, QTableView::item:alternate {{
    background: {c.surface};
}}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background: {c.primary};
    color: white;
}}

/* Header */
QHeaderView::section {{
    background: {c.surface};
    border: none;
    border-right: 1px solid {c.border};
    border-bottom: 1px solid {c.border};
    padding: 6px;
}}
"""

    @staticmethod
    def _adjust_color(color: str, amount: int) -> str:
        """Adjust a hex color's brightness.

        Args:
            color: Hex color string.
            amount: Amount to adjust (-255 to 255).

        Returns:
            Adjusted hex color string.
        """
        qcolor = QColor(color)
        h, s, l, a = qcolor.getHslF()
        l = max(0.0, min(1.0, l + amount / 255.0))
        qcolor.setHslF(h, s, l, a)
        return qcolor.name()

    # Color utility methods

    def get_state_color(self, state: str) -> str:
        """Get color for a named state.

        Args:
            state: State name (success, warning, error, info, etc.)

        Returns:
            Hex color string.
        """
        state_colors = {
            "success": self._colors.success,
            "warning": self._colors.warning,
            "error": self._colors.error,
            "info": self._colors.info,
            "connected": self._colors.connected,
            "disconnected": self._colors.disconnected,
        }
        return state_colors.get(state, self._colors.text)

    def get_background_for_state(self, state: str) -> str:
        """Get muted background color for a state.

        Args:
            state: State name.

        Returns:
            Hex color string suitable for background.
        """
        base_color = self.get_state_color(state)

        if self.is_dark:
            # Darken for dark theme
            return self._adjust_color(base_color, -100)
        else:
            # Lighten for light theme
            return self._adjust_color(base_color, 100)
