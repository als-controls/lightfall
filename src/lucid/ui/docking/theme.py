"""Docking theme integration.

Generates stylesheets for QDockWidget and the docking system that match
the application theme.

When the active theme defines a `sea` color (distinct from `background`),
panels get the "Islands" treatment: rounded corners, visible gaps.

Color model (Islands dark):
    sea    (#27272A) — lighter, app background / gaps / visible in corners
    island (#1E1E22) — darker, panel title bars + content + headers

Qt does NOT clip children to parent border-radius. So we round the
children themselves:
    - PanelTitleBar: island bg + top rounding
    - Content widget: island bg + bottom rounding
    - Edge-touching children (text edits, tables): bottom rounding
The QDockWidget is transparent with margin — the sea shows through
the gaps and the rounded corners.
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
GAP = 3              # sea gap around islands (margin, px)


def dump_dock_tree() -> None:
    """Debug helper: print the widget tree inside all QDockWidgets.

    Run from LUCID's Python console:
        from lucid.ui.docking.theme import dump_dock_tree
        dump_dock_tree()
    """
    from PySide6.QtWidgets import QApplication, QDockWidget, QWidget

    def _walk(widget: QWidget, indent: int = 0) -> None:
        name = widget.objectName() or "(no name)"
        cls = type(widget).__name__
        bg = widget.palette().color(widget.backgroundRole()).name()
        geo = widget.geometry()
        vis = "V" if widget.isVisible() else "H"
        print(
            f"{'  ' * indent}{cls} [{name}] bg={bg} "
            f"{geo.width()}x{geo.height()} {vis}"
        )
        for child in widget.children():
            if isinstance(child, QWidget):
                _walk(child, indent + 1)

    for w in QApplication.instance().allWidgets():
        if isinstance(w, QDockWidget) and w.isVisible():
            print(f"\n=== {w.objectName()} ===")
            _walk(w)
            print()


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

/* ==========================================================================
   QDockWidget — transparent shell with margin for sea gaps
   ========================================================================== */

QDockWidget {{
    background: transparent;
    border: none;
    {"margin: " + str(gap) + "px;" if islands else ""}
    titlebar-close-icon: url(none);
    titlebar-normal-icon: url(none);
}}

/* --------------------------------------------------------------------------
   Custom Panel Title Bar — island bg + top rounding
   -------------------------------------------------------------------------- */
#PanelTitleBar {{
    background: {island};
    border: none;
    {"border-top-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-top-right-radius: " + str(radius) + "px;" if islands else ""}
}}

#PanelTitleLabel {{
    color: {colors.text_secondary};
    font-weight: 600;
    font-size: 11px;
    background: transparent;
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

/* --------------------------------------------------------------------------
   Native title bar fallback (when no custom title bar is set)
   -------------------------------------------------------------------------- */
QDockWidget::title {{
    background: {island};
    border: none;
    {"border-top-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-top-right-radius: " + str(radius) + "px;" if islands else ""}
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
   Dock content — island bg + bottom rounding
   -------------------------------------------------------------------------- */
QDockWidget > QWidget {{
    background: {island};
    {"border-bottom-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-bottom-right-radius: " + str(radius) + "px;" if islands else ""}
}}

{"" if not islands else f"""
/* Edge-touching scrollable widgets — must inherit bottom rounding
   so they don't paint opaque rectangles over the rounded corners */
QDockWidget QPlainTextEdit,
QDockWidget QTextEdit,
QDockWidget QListView,
QDockWidget QTreeView,
QDockWidget QTableView,
QDockWidget QScrollArea {{
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

/* --------------------------------------------------------------------------
   Inner QMainWindow — the "sea"
   -------------------------------------------------------------------------- */
QMainWindow > QMainWindow {{
    background: {sea};
}}

/* --------------------------------------------------------------------------
   Central widget (e.g. logbook) — island with rounding + margin
   -------------------------------------------------------------------------- */
{"" if not islands else f"""
QMainWindow > QMainWindow > .QWidget {{
    background: {island};
    border-radius: {radius}px;
    margin: {gap}px;
}}
"""}

/* --------------------------------------------------------------------------
   QMainWindow separators — sea-colored gaps between islands
   -------------------------------------------------------------------------- */
QMainWindow::separator {{
    background: {sea};
    width: {max(gap * 2, 2)}px;
    height: {max(gap * 2, 2)}px;
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
