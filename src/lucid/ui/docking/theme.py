"""Docking theme integration.

Generates stylesheets for QDockWidget and the docking system that match
the application theme.

When the active theme defines a `sea` color (distinct from `background`),
panels get the "Islands" treatment: rounded corners, no hard borders,
with visible sea-colored gaps between panels.

Color model (Islands dark):
    sea    (#27272A) — lighter, the app background / gaps / corners
    island (#1E1E22) — darker, panel title bars + content + headers

The QDockWidget itself has sea background + border-radius. Its children
(title bar, content) are island-colored with NO rounding — the parent's
sea background peeks through the rounded corners.
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
   Icon Strip Sidebar — sits in the sea
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
   Custom Panel Title Bar — island color, NO rounding
   (rounding comes from the QDockWidget parent's sea background)
   -------------------------------------------------------------------------- */
#PanelTitleBar {{
    background: {island};
    border: none;
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
   QDockWidget — sea background + border-radius
   The sea peeks through the rounded corners. Children paint island
   on top without rounding, so the sea corners are visible.
   ========================================================================== */

QDockWidget {{
    background: {sea};
    {"border: none;" if islands else f"border: 1px solid {colors.border};"}
    {"border-radius: " + str(radius) + "px;" if islands else ""}
    titlebar-close-icon: url(none);
    titlebar-normal-icon: url(none);
}}

/* Native title bar — island, no rounding */
QDockWidget::title {{
    background: {island};
    border: none;
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
   Content inside dock widgets — island, no rounding
   -------------------------------------------------------------------------- */
QDockWidget > QWidget {{
    background: {island};
}}

{"" if not islands else f"""
/* Scrollable content widgets inside docks */
QDockWidget QPlainTextEdit,
QDockWidget QTextEdit,
QDockWidget QListView,
QDockWidget QTreeView,
QDockWidget QTableView {{
    border: none;
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

/* --------------------------------------------------------------------------
   Inner QMainWindow — the "sea"
   -------------------------------------------------------------------------- */
QMainWindow > QMainWindow {{
    background: {sea};
}}

/* --------------------------------------------------------------------------
   Central widget (e.g. logbook) — island with rounded corners
   The inner QMainWindow's sea background shows through the corners.
   -------------------------------------------------------------------------- */
{"" if not islands else f"""
QMainWindow > QMainWindow > .QWidget {{
    background: {island};
    border-radius: {radius}px;
}}
"""}

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
"""


# Backward compatibility alias
def generate_qtads_stylesheet(colors: ThemeColors) -> str:
    """Generate docking stylesheet (backward-compatible name).

    .. deprecated:: Use generate_docking_stylesheet() instead.
    """
    return generate_docking_stylesheet(colors)
