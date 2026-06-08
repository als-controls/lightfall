"""Theme manager for Lightfall application theming.

Provides application-wide theme control with support for:
- Light, dark, and system-following modes
- Plugin-based theme definitions
- Beamline-specific color accents
- Theme-aware color utilities
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.plugins.theme_plugin import ThemeDefinition, ThemePlugin


class Theme(Enum):
    """Available theme modes.

    Note: This enum is kept for backward compatibility. New code should
    use string-based theme names with ThemeManager.set_theme_by_name().
    """

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

    # Islands layout: "sea" is the visible gap behind floating panels.
    # When empty, falls back to background (non-Islands themes unchanged).
    sea: str = ""

    def __post_init__(self) -> None:
        """Set default state colors based on theme."""
        if not self.connected:
            self.connected = self.success
        if not self.disconnected:
            self.disconnected = self.error
        if not self.sea:
            self.sea = self.background

    @classmethod
    def from_definition(cls, definition: ThemeDefinition) -> ThemeColors:
        """Create ThemeColors from a ThemeDefinition.

        Args:
            definition: ThemeDefinition from a theme plugin.

        Returns:
            ThemeColors instance with the same values.
        """
        return cls(
            primary=definition.primary,
            secondary=definition.secondary,
            success=definition.success,
            warning=definition.warning,
            error=definition.error,
            info=definition.info,
            background=definition.background,
            surface=definition.surface,
            text=definition.text,
            text_secondary=definition.text_secondary,
            border=definition.border,
            connected=definition.connected,
            disconnected=definition.disconnected,
            sea=definition.sea,
        )


# Pre-defined theme color schemes (fallback during early init)
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

# Fallback color schemes for early init (before plugins load)
_FALLBACK_COLORS: dict[str, ThemeColors] = {
    "light": LIGHT_COLORS,
    "dark": SLATE_COLORS,
    "slate": SLATE_COLORS,
    "darkblue": DARKBLUE_COLORS,
}


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
    - Plugin-based theme definitions via ThemeRegistry
    - System theme detection and following
    - Beamline-specific customization
    - Theme-aware color utilities
    - Stylesheet generation

    Signals:
        theme_changed: Emitted when theme mode changes (passes theme name as str).
        colors_changed: Emitted when colors are updated.

    Example:
        >>> manager = ThemeManager.get_instance()
        >>> manager.set_theme_by_name("slate")
        >>> manager.colors.background
        '#1e1e1e'
        >>> manager.get_available_themes()
        [{'name': 'light', 'display_name': 'Light', 'is_dark': False}, ...]
    """

    theme_changed = Signal(str)  # Now emits theme name as string
    colors_changed = Signal()

    _instance: ThemeManager | None = None
    _lock = threading.RLock()

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the theme manager."""
        super().__init__(parent)
        # Theme name: "system" or a registered theme name
        self._theme_name: str = "system"
        # The effective (resolved) theme name (never "system")
        self._effective_theme_name: str = "light"
        # Current theme plugin (may be None during early init)
        self._current_theme_plugin: ThemePlugin | None = None
        # CSS overrides from current theme
        self._css_overrides: str = ""
        self._colors = LIGHT_COLORS
        self._beamline_theme: BeamlineTheme | None = None
        self._custom_stylesheets: dict[str, str] = {}
        # Islands layout (rounded floating panel cards on a sea canvas) is a
        # user preference applied on top of any theme, not tied to the theme's
        # colors. Default on; AppearanceSettingsPlugin syncs it from prefs.
        self._islands_mode: bool = True

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
        """Current theme mode setting (for backward compatibility).

        Deprecated: Use theme_name instead.
        """
        try:
            return Theme(self._theme_name)
        except ValueError:
            # Custom theme name not in enum
            if self.is_dark:
                return Theme.DARK
            return Theme.LIGHT

    @property
    def theme_name(self) -> str:
        """Current theme name setting.

        Returns "system" if following system preference, otherwise the
        theme plugin name (e.g., "light", "slate", "darkblue").
        """
        return self._theme_name

    @property
    def effective_theme(self) -> Theme:
        """Actual theme being used (for backward compatibility).

        Deprecated: Use effective_theme_name instead.
        """
        try:
            return Theme(self._effective_theme_name)
        except ValueError:
            # Custom theme name not in enum
            if self.is_dark:
                return Theme.DARK
            return Theme.LIGHT

    @property
    def effective_theme_name(self) -> str:
        """Actual theme name being used (resolves "system" to actual theme)."""
        return self._effective_theme_name

    @property
    def is_dark(self) -> bool:
        """Whether the effective theme is dark."""
        if self._current_theme_plugin:
            return self._current_theme_plugin.is_dark
        # Fallback for known themes
        return self._effective_theme_name in ("dark", "slate", "darkblue")

    @property
    def colors(self) -> ThemeColors:
        """Current theme colors."""
        return self._colors

    @property
    def islands_mode(self) -> bool:
        """Whether the Islands layout is applied (independent of the theme)."""
        return self._islands_mode

    def set_islands_mode(self, enabled: bool) -> None:
        """Enable/disable the Islands layout for any theme.

        Re-applies the stylesheet (regenerated with the new flag) via the
        theme_changed signal, the same path a theme switch uses.

        Args:
            enabled: True to apply rounded floating panel cards on a sea
                canvas; False for a flat layout.
        """
        if enabled == self._islands_mode:
            return
        self._islands_mode = enabled
        logger.info("Islands layout {}", "enabled" if enabled else "disabled")
        self.theme_changed.emit(self._theme_name)

    @property
    def beamline_theme(self) -> BeamlineTheme | None:
        """Current beamline-specific theme."""
        return self._beamline_theme

    def set_theme(self, theme: Theme) -> None:
        """Set the theme mode (for backward compatibility).

        Args:
            theme: The theme mode to use.

        Note: Prefer set_theme_by_name() for new code.
        """
        # Map "dark" to "slate" for backward compatibility
        theme_name = theme.value
        if theme_name == "dark":
            theme_name = "slate"
        self.set_theme_by_name(theme_name)

    def set_theme_by_name(self, theme_name: str) -> None:
        """Set the theme by name.

        Args:
            theme_name: Theme name ("system" or a registered theme name).
                        "dark" is mapped to "slate" for backward compatibility.
        """
        # Map "dark" to "slate" for backward compatibility
        if theme_name == "dark":
            theme_name = "slate"

        if theme_name == self._theme_name:
            return

        old_name = self._theme_name
        self._theme_name = theme_name
        self._update_effective_theme()

        logger.info("Theme changed: {} -> {}", old_name, theme_name)
        self.theme_changed.emit(theme_name)

    def get_available_themes(self) -> list[dict[str, Any]]:
        """Get all available themes for UI display.

        Returns:
            List of theme info dicts with 'name', 'display_name', 'is_dark'.
            Includes a "System" option first.
        """
        themes = [
            {
                "name": "system",
                "display_name": "System",
                "is_dark": None,  # Follows system
            }
        ]

        # Get themes from registry
        try:
            from lightfall.ui.theme.registry import ThemeRegistry

            registry = ThemeRegistry.get_instance()
            for plugin in registry.get_all():
                themes.append({
                    "name": plugin.name,
                    "display_name": plugin.display_name,
                    "is_dark": plugin.is_dark,
                })
        except ImportError:
            # Registry not available, use fallback
            logger.debug("ThemeRegistry not available, using fallback themes")
            themes.extend([
                {"name": "light", "display_name": "Light", "is_dark": False},
                {"name": "slate", "display_name": "Slate (Dark)", "is_dark": True},
                {"name": "darkblue", "display_name": "Dark Blue", "is_dark": True},
            ])

        return themes

    def _update_effective_theme(self) -> None:
        """Update the effective theme based on current settings."""
        if self._theme_name == "system":
            self._effective_theme_name = self._get_system_theme_name()
        else:
            self._effective_theme_name = self._theme_name

        # Try to get theme from registry
        self._current_theme_plugin = None
        self._css_overrides = ""

        try:
            from lightfall.ui.theme.registry import ThemeRegistry

            registry = ThemeRegistry.get_instance()
            plugin = registry.get(self._effective_theme_name)

            if plugin:
                self._current_theme_plugin = plugin
                definition = plugin.get_theme_definition()
                self._colors = ThemeColors.from_definition(definition)
                self._css_overrides = definition.css_overrides
            else:
                # Theme not in registry, use fallback
                self._use_fallback_colors()
        except ImportError:
            # Registry not available, use fallback
            self._use_fallback_colors()

        # Apply beamline customizations
        if self._beamline_theme:
            self._apply_beamline_colors()

        self.colors_changed.emit()

    def _use_fallback_colors(self) -> None:
        """Use fallback colors when registry is not available."""
        fallback = _FALLBACK_COLORS.get(self._effective_theme_name)
        if fallback:
            self._colors = ThemeColors(**vars(fallback))
        else:
            # Unknown theme, default to light
            self._colors = ThemeColors(**vars(LIGHT_COLORS))

    def _get_system_theme_name(self) -> str:
        """Get theme name based on system preference."""
        is_dark = self._detect_system_is_dark()

        # Try to get appropriate theme from registry
        try:
            from lightfall.ui.theme.registry import ThemeRegistry

            registry = ThemeRegistry.get_instance()
            plugin = registry.get_theme_for_system(is_dark)
            if plugin:
                return plugin.name
        except ImportError:
            pass

        # Fallback
        return "slate" if is_dark else "light"

    def _detect_system_is_dark(self) -> bool:
        """Detect if the system is using dark mode."""
        app = QApplication.instance()
        if app is None:
            return False

        palette = app.palette()
        window_color = palette.color(QPalette.ColorRole.Window)

        # Calculate luminance
        luminance = (
            0.299 * window_color.redF()
            + 0.587 * window_color.greenF()
            + 0.114 * window_color.blueF()
        )

        return luminance < 0.5

    def _detect_system_theme(self) -> Theme:
        """Detect if the system is using dark mode (for backward compatibility)."""
        return Theme.DARK if self._detect_system_is_dark() else Theme.LIGHT

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

        logger.debug("Applied {} theme to application", self._effective_theme_name)

    def _apply_dark_palette(self, app: QApplication) -> None:
        """Apply a dark color palette to the application."""
        palette = QPalette()

        # Window color = sea (the app background / gaps between panels).
        # QDockWidget paints from this role directly, ignoring QSS.
        palette.setColor(QPalette.ColorRole.Window, QColor(self._colors.sea))
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
        base_stylesheet = f"""
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
        # Append theme-specific CSS overrides
        if self._css_overrides:
            base_stylesheet += f"\n/* Theme-specific overrides */\n{self._css_overrides}"

        # Append docking stylesheet if available
        try:
            from lightfall.ui.docking.theme import generate_docking_stylesheet
            base_stylesheet += (
                f"\n{generate_docking_stylesheet(c, islands=self._islands_mode)}"
            )
        except ImportError:
            pass

        return base_stylesheet

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
        h, s, lightness, a = qcolor.getHslF()
        lightness = max(0.0, min(1.0, lightness + amount / 255.0))
        qcolor.setHslF(h, s, lightness, a)
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
