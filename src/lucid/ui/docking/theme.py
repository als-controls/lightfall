"""Docking theme integration.

Generates stylesheets for QDockWidget and the docking system that match
the application theme.

When the active theme defines a `sea` color (distinct from `background`),
panels get the "Islands" treatment: rounded corners, no hard borders,
with visible sea-colored gaps between panels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lucid.ui.theme.manager import ThemeColors

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
RADIUS = 10          # panel corner radius
RADIUS_SM = 6        # small elements (buttons, inputs)
GAP = 6              # sea gap between islands (px)


def _is_islands_mode(colors: ThemeColors) -> bool:
    """Check whether the current theme uses Islands layout."""
    return bool(colors.sea) and colors.sea != colors.background


def generate_docking_stylesheet(colors: ThemeColors) -> str:
    """Generate a stylesheet for the docking system.

    This replaces the old generate_qtads_stylesheet() and targets
    native QDockWidget + our custom widgets.

    Args:
        colors: The current theme colors.

    Returns:
        CSS stylesheet string.
    """
    islands = _is_islands_mode(colors)
    sea = colors.sea if islands else colors.background
    island = colors.surface if islands else colors.surface

    radius = RADIUS if islands else 0
    gap = GAP if islands else 0

    return f"""
/* ==========================================================================
   Icon Strip Sidebar
   ========================================================================== */
#IconStripSidebar {{
    background: {sea};
    border-right: {"none" if islands else f"1px solid {colors.border}"};
}}

#IconStripSidebar QToolButton {{
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 3px;
    background: transparent;
}}

#IconStripSidebar QToolButton:hover {{
    background: {colors.border};
}}

#IconStripSidebar QToolButton:checked {{
    background: {colors.primary};
}}

#IconStripSidebar QToolButton:checked:hover {{
    background: {colors.primary};
}}

#IconStripSeparator {{
    background: {colors.border};
}}

#IconStripDropIndicator {{
    background: {colors.primary};
    border-radius: 1px;
}}

/* --------------------------------------------------------------------------
   Custom Panel Title Bar — island (dark) surface
   -------------------------------------------------------------------------- */
#PanelTitleBar {{
    background: {island};
    border-bottom: none;
    {"border-top-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-top-right-radius: " + str(radius) + "px;" if islands else ""}
}}

#PanelTitleLabel {{
    color: {colors.text_secondary};
    font-weight: 600;
    font-size: 11px;
}}

#PanelTitleCloseButton {{
    background: transparent;
    border: none;
    border-radius: 3px;
    padding: 2px;
}}

#PanelTitleCloseButton:hover {{
    background: {colors.border};
}}

#PanelTitleCloseButton:pressed {{
    background: {colors.text_secondary};
}}

/* ==========================================================================
   QDockWidget — the "islands"
   ========================================================================== */

QDockWidget {{
    background: {sea};
    {"border: none;" if islands else f"border: 1px solid {colors.border};"}
    {"border-radius: " + str(radius) + "px;" if islands else ""}
    titlebar-close-icon: url(none);
    titlebar-normal-icon: url(none);
}}

/* Native title bar — island (dark) surface */
QDockWidget::title {{
    background: {island};
    {"border-top-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-top-right-radius: " + str(radius) + "px;" if islands else ""}
    border-bottom: none;
    padding: 6px 8px;
    color: {colors.text_secondary};
    font-weight: 600;
    font-size: 11px;
}}

QDockWidget::close-button,
QDockWidget::float-button {{
    border: none;
    background: transparent;
    padding: 2px;
    border-radius: 3px;
}}

QDockWidget::close-button:hover,
QDockWidget::float-button:hover {{
    background: {colors.border};
}}

/* --------------------------------------------------------------------------
   Inner QMainWindow — the "sea"
   -------------------------------------------------------------------------- */

/* The inner QMainWindow hosts dock widgets; its background is the sea */
QMainWindow > QMainWindow {{
    background: {sea};
}}

/* --------------------------------------------------------------------------
   QMainWindow separators — sea-colored gaps between islands
   -------------------------------------------------------------------------- */
QMainWindow::separator {{
    background: {sea};
    width: {max(gap, 2)}px;
    height: {max(gap, 2)}px;
}}

QMainWindow::separator:hover {{
    background: {colors.primary};
}}

/* --------------------------------------------------------------------------
   Content inside dock widgets — island surface with rounding
   -------------------------------------------------------------------------- */
{"" if not islands else f"""
QDockWidget > QWidget {{
    background: {island};
    border-bottom-left-radius: {radius}px;
    border-bottom-right-radius: {radius}px;
}}

/* Scrollable content widgets inside docks inherit bottom rounding */
QDockWidget QPlainTextEdit,
QDockWidget QTextEdit,
QDockWidget QListView,
QDockWidget QTreeView,
QDockWidget QTableView {{
    border: none;
    border-radius: 0px;
    border-bottom-left-radius: {radius}px;
    border-bottom-right-radius: {radius}px;
    background: {island};
}}

/* Table/tree headers inside docks — island surface */
QDockWidget QHeaderView::section {{
    background: {island};
    color: {colors.text_secondary};
    border: none;
    border-bottom: 1px solid {colors.border};
    border-right: 1px solid {colors.border};
    padding: 6px 8px;
    font-weight: 600;
    font-size: 12px;
}}
"""}
"""


# Backward compatibility alias
def generate_qtads_stylesheet(colors: ThemeColors) -> str:
    """Generate docking stylesheet (backward-compatible name).

    .. deprecated:: Use generate_docking_stylesheet() instead.
    """
    return generate_docking_stylesheet(colors)
