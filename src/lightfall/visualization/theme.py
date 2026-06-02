"""Theme integration utilities for visualization widgets.

Provides consistent theming across all visualization types with
PyQtGraph-specific color handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from lucid.ui.theme.manager import ThemeColors


@dataclass
class VisualizationColors:
    """Color palette for visualization widgets.

    Provides named colors optimized for data visualization with
    good contrast and accessibility.

    Attributes:
        background: Plot background color.
        foreground: Text and axis color.
        grid: Grid line color.
        border: Border color.
        primary_line: Primary data line color.
        secondary_line: Secondary data line color.
        highlight: Selection/highlight color.
        fit_line: Curve fit overlay color.
        error_band: Error band fill color.
        colormap: Default colormap name for images.
    """

    background: str = "#1e1e1e"
    foreground: str = "#d4d4d4"
    grid: str = "#3e3e3e"
    border: str = "#4e4e4e"
    primary_line: str = "#3b82f6"  # Blue
    secondary_line: str = "#22c55e"  # Green
    highlight: str = "#f59e0b"  # Amber
    fit_line: str = "#ef4444"  # Red (dashed)
    error_band: str = "#3b82f680"  # Blue with alpha
    colormap: str = "viridis"

    # Additional line colors for multiple traces
    line_colors: tuple[str, ...] = (
        "#3b82f6",  # Blue
        "#22c55e",  # Green
        "#f59e0b",  # Amber
        "#ef4444",  # Red
        "#8b5cf6",  # Purple
        "#06b6d4",  # Cyan
        "#ec4899",  # Pink
        "#84cc16",  # Lime
    )


# Pre-defined color schemes
DARK_VIZ_COLORS = VisualizationColors(
    background="#1e1e1e",
    foreground="#d4d4d4",
    grid="#3e3e3e",
    border="#4e4e4e",
    primary_line="#3b82f6",
    secondary_line="#22c55e",
    highlight="#f59e0b",
    fit_line="#ef4444",
    error_band="#3b82f680",
    colormap="viridis",
)

LIGHT_VIZ_COLORS = VisualizationColors(
    background="#ffffff",
    foreground="#1f2937",
    grid="#e5e7eb",
    border="#d1d5db",
    primary_line="#2563eb",
    secondary_line="#16a34a",
    highlight="#d97706",
    fit_line="#dc2626",
    error_band="#2563eb40",
    colormap="viridis",
)


def get_visualization_colors(is_dark: bool = True) -> VisualizationColors:
    """Get visualization color palette based on theme.

    Args:
        is_dark: Whether dark theme is active.

    Returns:
        VisualizationColors for the theme.
    """
    return DARK_VIZ_COLORS if is_dark else LIGHT_VIZ_COLORS


def colors_from_theme(theme_colors: ThemeColors, is_dark: bool) -> VisualizationColors:
    """Create visualization colors from ThemeColors.

    Maps application theme colors to visualization-specific colors,
    ensuring good contrast for data display.

    Args:
        theme_colors: ThemeColors from ThemeManager.
        is_dark: Whether the theme is dark.

    Returns:
        VisualizationColors derived from theme.
    """
    return VisualizationColors(
        background=theme_colors.background,
        foreground=theme_colors.text,
        grid=theme_colors.border,
        border=theme_colors.border,
        primary_line=theme_colors.primary,
        secondary_line=theme_colors.success,
        highlight=theme_colors.warning,
        fit_line=theme_colors.error,
        error_band=f"{theme_colors.primary}80",  # Add alpha
        colormap="viridis" if is_dark else "plasma",
    )


class ThemedVisualizationMixin:
    """Mixin providing theme integration for visualization widgets.

    Add this mixin to visualization widgets to get consistent theme
    handling with automatic updates when the theme changes.

    Example:
        >>> class MyPlot(ThemedVisualizationMixin, QWidget):
        ...     def __init__(self):
        ...         super().__init__()
        ...         self._setup_theme()
        ...
        ...     def _apply_viz_colors(self, colors: VisualizationColors):
        ...         self._plot.setBackground(colors.background)
    """

    _viz_colors: VisualizationColors

    def _setup_theme(self) -> None:
        """Initialize theme support.

        Call this in __init__ after Qt initialization.
        """
        self._viz_colors = DARK_VIZ_COLORS
        self._update_viz_colors()
        self._connect_to_theme_changes()

    def _update_viz_colors(self) -> None:
        """Update visualization colors from current theme."""
        try:
            from lucid.ui.theme import ThemeManager

            theme = ThemeManager.get_instance()
            self._viz_colors = colors_from_theme(theme.colors, theme.is_dark)
            self._apply_viz_colors(self._viz_colors)
        except ImportError:
            # Theme system not available, use defaults
            self._apply_viz_colors(self._viz_colors)

    def _connect_to_theme_changes(self) -> None:
        """Connect to theme change signals."""
        try:
            from lucid.ui.theme import ThemeManager

            theme = ThemeManager.get_instance()
            theme.colors_changed.connect(self._update_viz_colors)
        except ImportError:
            pass

    def _apply_viz_colors(self, colors: VisualizationColors) -> None:
        """Apply visualization colors.

        Override to update visualization with new colors.

        Args:
            colors: New visualization colors.
        """
        pass

    def get_line_color(self, index: int) -> str:
        """Get a line color by index.

        Cycles through available line colors.

        Args:
            index: Line index (0-based).

        Returns:
            Hex color string.
        """
        colors = self._viz_colors.line_colors
        return colors[index % len(colors)]


def apply_pyqtgraph_theme(is_dark: bool = True) -> None:
    """Apply theme to PyQtGraph global settings.

    Call this once at application startup to configure PyQtGraph
    for the current theme.

    Args:
        is_dark: Whether dark theme is active.
    """
    try:
        import pyqtgraph as pg

        colors = get_visualization_colors(is_dark)

        # Set default background
        pg.setConfigOption("background", colors.background)
        pg.setConfigOption("foreground", colors.foreground)

        # Enable antialiasing for smoother plots
        pg.setConfigOption("antialias", True)

        logger.debug("Applied PyQtGraph theme (dark={})", is_dark)
    except ImportError:
        logger.warning("PyQtGraph not available for theming")


def get_colormap(name: str = "viridis") -> Any:
    """Get a colormap by name for image visualization.

    Args:
        name: Colormap name (e.g., "viridis", "plasma", "inferno").

    Returns:
        PyQtGraph colormap or None if not available.
    """
    try:
        import pyqtgraph as pg

        # PyQtGraph has built-in colormaps
        return pg.colormap.get(name)
    except (ImportError, KeyError):
        return None


def make_colormap_lut(name: str = "viridis", n_colors: int = 256) -> Any:
    """Create a lookup table for a colormap.

    Args:
        name: Colormap name.
        n_colors: Number of colors in the table.

    Returns:
        Numpy array of RGBA values or None.
    """
    try:
        import pyqtgraph as pg

        cmap = pg.colormap.get(name)
        if cmap is None:
            return None

        return cmap.getLookupTable(nPts=n_colors)
    except ImportError:
        return None
