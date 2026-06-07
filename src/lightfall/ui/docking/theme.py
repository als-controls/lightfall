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
    - Panel content: island bg + bottom rounding
The QDockWidget has sea bg so rounded corner areas show sea color.

Widget tree (from dump_dock_tree):
    PanelDockWidget [dock_*]              ← sea bg, border-radius
      QAbstractButton [qt_dockwidget_*]   ← hidden (custom title bar)
      SomePanel [lightfall.panels.*]          ← island bg, bottom rounding
      PanelTitleBar [PanelTitleBar]        ← island bg, top rounding
        QLabel [PanelTitleLabel]
        QToolButton [PanelTitleButton]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightfall.ui.theme.manager import ThemeColors

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
RADIUS = 10          # panel corner radius
RADIUS_SM = 6        # small elements (buttons, inputs)
GAP = 3              # sea gap around islands (margin, px)


def dump_dock_tree() -> None:
    """Debug helper: print the widget tree inside all QDockWidgets.

    Run from Lightfall's Python console:
        from lightfall.ui.docking.theme import dump_dock_tree
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

    # Selected (checked) sidebar buttons: in Islands mode use the surface
    # color so the active button reads as part of the island it opens, rather
    # than the loud primary accent. Non-islands themes keep the primary
    # highlight unchanged.
    checked_bg = island if islands else colors.primary

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
    background: {checked_bg};
}}

#IconStripSidebar QToolButton:checked:hover {{
    background: {checked_bg};
}}

#IconStripSeparator {{
    background: {colors.border};
}}

#IconStripDropIndicator {{
    background: {colors.primary};
    border-radius: 1px;
}}

/* ==========================================================================
   QDockWidget — sea background so corners show sea color
   ========================================================================== */

QDockWidget {{
    background: {sea};
    border: none;
    titlebar-close-icon: url(none);
    titlebar-normal-icon: url(none);
}}

/* --------------------------------------------------------------------------
   Custom Panel Title Bar — island bg + top rounding
   Targeted by object name so it doesn't conflict with QDockWidget > QWidget
   -------------------------------------------------------------------------- */
#PanelTitleBar {{
    background: {island};
    border: none;
    {"border-top-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-top-right-radius: " + str(radius) + "px;" if islands else ""}
}}

#PanelTitleLabel {{
    color: {colors.text_secondary};
    background: transparent;
    font-weight: 600;
    font-size: 11px;
}}

#PanelTitleButton {{
    background: transparent;
    border: none;
    border-radius: 3px;
    padding: 2px;
}}

#PanelTitleButton:hover {{
    background: {colors.border};
}}

#PanelTitleButton:pressed {{
    background: {colors.text_secondary};
}}

#PanelTitleSeparator {{
    color: {colors.border};
}}

/* --------------------------------------------------------------------------
   Native title bar fallback (hidden when custom title bar is set)
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
   Panel content — island bg + bottom rounding
   The title bar is also a direct child of QDockWidget, but we target
   it by #PanelTitleBar above. Here we use QDockWidget > QWidget to
   catch the panel widget and give it bottom rounding.
   Note: this also matches PanelTitleBar (which is a QWidget), so
   PanelTitleBar's more-specific #id selector overrides it.
   -------------------------------------------------------------------------- */
QDockWidget > QWidget {{
    background: {island};
    {"border-bottom-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-bottom-right-radius: " + str(radius) + "px;" if islands else ""}
}}

{"" if not islands else f'''
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

/* Panel interiors and rounded corners. A BasePanel wraps its content in a
   QScrollArea (viewport + scrolled child). Both autofill the Window palette
   role (= sea) and, as opaque squares, paint sea AND square off the panel's
   rounded corners (Qt doesn't clip children to a parent's border-radius).
   Make the whole scroll subtree transparent so the rounded surface painted
   by the panel's ancestor shows through — the central BasePanel
   (#InnerDockWindow > QWidget, all-rounded) for the logbook, and the dock's
   content wrapper (QDockWidget > QWidget, bottom-rounded) for dock panels.
   Targeting QScrollArea descendants keeps styled controls (inputs, lists)
   and their own backgrounds intact.

   Use background-color: rgba(0,0,0,0), NOT `background: transparent`. For a
   widget with autoFillBackground=True (the viewport and scrolled child),
   the `transparent` shorthand resolves to "no brush" and Qt falls back to
   filling with the palette Window brush (= sea) — so it never goes
   transparent. An explicit zero-alpha background-color overrides the
   autofill brush and genuinely paints nothing. */
QDockWidget QScrollArea,
QDockWidget QScrollArea > QWidget,
QDockWidget QScrollArea > QWidget > QWidget,
#InnerDockWindow > QWidget QScrollArea,
#InnerDockWindow > QWidget QScrollArea > QWidget,
#InnerDockWindow > QWidget QScrollArea > QWidget > QWidget {{
    background-color: rgba(0, 0, 0, 0);
}}

/* List widgets and frames inside docks — island surface, no frame */
QDockWidget QListWidget,
QDockWidget QListView,
QDockWidget QFrame {{
    background: {island};
    border: none;
}}

/* EntryListWidget — full island rounding so it doesn't paint over
   the panel's rounded corners */
#EntryListWidget {{
    background: {island};
    border: none;
    border-radius: {radius}px;
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

/* Toolbars inside panels */
QDockWidget QToolBar {{
    background: {island};
    border: none;
}}

QDockWidget QToolBar QToolButton {{
    background: transparent;
}}
'''}

/* --------------------------------------------------------------------------
   Inner QMainWindow — the "sea"
   -------------------------------------------------------------------------- */
#InnerDockWindow {{
    background: {sea};
}}

/* --------------------------------------------------------------------------
   Central widget (e.g. logbook) — island with rounding + margin
   -------------------------------------------------------------------------- */
{"" if not islands else f'''
#InnerDockWindow > QWidget {{
    background: {island};
    border-radius: {radius}px;
    margin: {gap}px;
}}

/* The central widget (e.g. logbook) wraps its content in a BasePanel
   QScrollArea that fills edge-to-edge. Without matching island bg + rounding
   it paints opaque square corners over the central island's rounded corners.
   The double child-combinator targets ONLY the central widget's own scroll
   area (InnerDockWindow > centralWidget > QScrollArea); dock-hosted scroll
   areas live under QDockWidget > BasePanel and are never matched here. */
#InnerDockWindow > QWidget > QScrollArea {{
    background: {island};
    border: none;
    border-radius: {radius}px;
}}
'''}

/* --------------------------------------------------------------------------
   Splitters — island colored with rounded corners
   -------------------------------------------------------------------------- */
{"" if not islands else f'''
QSplitter {{
    background: {island};
    border-radius: {radius}px;
}}

QSplitter::handle {{
    background: {colors.border};
}}

QSplitter::handle:horizontal {{
    width: 2px;
}}

QSplitter::handle:vertical {{
    height: 2px;
}}

QSplitter::handle:hover {{
    background: {colors.primary};
}}
'''}

/* --------------------------------------------------------------------------
   Scrollbars — rounded handles
   -------------------------------------------------------------------------- */
{"" if not islands else f'''
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: {colors.border};
    border-radius: 4px;
    min-height: 30px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: {colors.text_secondary};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
    border: none;
}}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: {colors.border};
    border-radius: 4px;
    min-width: 30px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {colors.text_secondary};
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
    border: none;
}}

QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
'''}

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
