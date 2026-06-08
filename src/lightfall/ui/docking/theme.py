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
   QDockWidget — the canvas BEHIND the panel card: sea. The panel card (title
   bar + body) is surface with rounded corners; this sea shows at those
   rounded corners (and in the separator gaps), making the card read as a
   floating surface island.
   ========================================================================== */

QDockWidget {{
    background-color: {sea};
    border: none;
    titlebar-close-icon: url(none);
    titlebar-normal-icon: url(none);
}}

/* --------------------------------------------------------------------------
   Custom Panel Title Bar — island bg + top rounding
   Targeted by object name so it doesn't conflict with QDockWidget > QWidget
   -------------------------------------------------------------------------- */
#PanelTitleBar {{
    background-color: {island};
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

/* Toggle actions (auto-scroll, target-capture, ...) show their active
   state with the accent color. */
#PanelTitleButton:checked {{
    background: {colors.primary};
}}

#PanelTitleButton:checked:hover {{
    background: {colors.primary};
}}

/* Hide Qt's menu-indicator arrow on dropdown title bar buttons — at 20x20
   it overwhelms the icon. The icon itself signals the action. */
#PanelTitleButton::menu-indicator {{
    image: none;
    width: 0;
    height: 0;
}}

#PanelTitleSeparator {{
    color: {colors.border};
}}

/* --------------------------------------------------------------------------
   Native title bar fallback (hidden when custom title bar is set)
   -------------------------------------------------------------------------- */
QDockWidget::title {{
    background-color: {island};
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
    background-color: {island};
    {"border-bottom-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-bottom-right-radius: " + str(radius) + "px;" if islands else ""}
}}

{"" if not islands else f'''
/* ==========================================================================
   Islands styling model — three layers, applied consistently:

     SHELL     The rounded surface "card". The ONLY layer that paints surface.
               QDockWidget > QWidget (panel proxy, bottom-rounded),
               #InnerDockWindow > QWidget (central widget, all-rounded),
               QDialog, QStackedWidget, QTabWidget::pane.

     CONTAINER Structural widgets inside a shell. ALWAYS transparent so the
               shell's surface + rounded corners show through. They must never
               paint their own background: Qt does not clip children to a
               parent's border-radius, so an opaque container re-squares the
               rounded corner — only transparency composes.
               QScrollArea (+ viewport + scrolled child), QFrame, and the
               whole dock-panel subtree (TheaterProxy descendants).

     CONTROL   Interactive widgets that own their color: inputs (input color),
               item-view rows/headers, push buttons (sea). From the per-theme
               css_overrides + the polish rules below.

   New plugins inherit this for free — their frames/scroll areas are
   containers (transparent), so only a genuinely new *control* type ever needs
   a colour. Use background-color: rgba(0,0,0,0), NOT the `transparent`
   shorthand: containers' viewport/scrolled child autofill the Window (sea)
   palette role, and the shorthand falls back to that brush, whereas an
   explicit zero-alpha background-color genuinely paints nothing.
   ========================================================================== */

/* CONTAINERS — transparent. Global QScrollArea covers nested and future
   scroll areas (e.g. inside the LogbookPanel) without per-widget rules. */
QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget,
QDockWidget QFrame,
#InnerDockWindow QFrame,
#EntryListWidget {{
    background-color: rgba(0, 0, 0, 0);
}}

/* Docked panel = a rounded surface card floating on the sea (the QDockWidget
   canvas behind it). The panel widget — the first QWidget under the proxy —
   is the surface card body, with bottom rounding; the title bar above gives
   the top rounding. Painting the card body directly (not via transparency)
   is reliable: a docked QDockWidget paints nothing and the TheaterProxy
   (a QStackedWidget) doesn't paint behind its page, so transparent content
   would fall through to the sea. The card's content (deeper widgets) is
   transparent so the rounded surface + sea-revealing corners show.
   DESCENDANT combinator (not '>'): Qt's '>' does not match QStackedWidget
   pages, so '#TheaterProxy > QWidget' silently misses the panel. (The central
   widget is NOT proxy-wrapped — it rounds against its own sea-gap margin.) */
#TheaterProxy QWidget {{
    background-color: {island};
    border-bottom-left-radius: {radius}px;
    border-bottom-right-radius: {radius}px;
}}
#TheaterProxy QWidget QWidget {{
    background-color: rgba(0, 0, 0, 0);
    border-radius: 0px;
}}

/* PanelTitleBar is the card's top SHELL, but it's a QFrame — so the container
   QFrame transparency above (#InnerDockWindow QFrame) matches it and would
   blank it to the sea behind. Re-assert surface with a higher-precedence
   selector (id + type, emitted after the container rules) so the title bar
   reads as part of the surface card. Matches docked and floating (the title
   bar is always a child of the QDockWidget). */
QDockWidget #PanelTitleBar {{
    background-color: {island};
}}

/* Table/tree headers inside docks — island surface */
QDockWidget QHeaderView::section {{
    background-color: {island};
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
    background-color: {island};
    border: none;
}}

QDockWidget QToolBar QToolButton {{
    background: transparent;
}}

/* --------------------------------------------------------------------------
   Island widget polish — applied app-wide in islands themes. Appended after
   the per-theme css_overrides so these win for all islands themes.
   -------------------------------------------------------------------------- */

/* Tabs styled as rounded-rect buttons (like QPushButton): surface and
   borderless when idle, primary with a border when active. */
QTabBar::tab {{
    background-color: {island};
    color: {colors.text};
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 5px 12px;
    margin: 2px;
}}
QTabBar::tab:hover:!selected {{
    background: {colors.border};
}}
QTabBar::tab:selected {{
    background: {colors.primary};
    color: white;
    border: 1px solid {colors.border};
}}

/* Tab content pane: flat on surface, with a 1px sea line separating it
   from the tab bar above. */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {sea};
    background-color: {island};
}}

/* QGroupBox: section-divider look — the title sits on surface and a single
   1px sea line runs across the top instead of a full raised box border. */
QGroupBox {{
    border: none;
    border-top: 1px solid {sea};
}}
QGroupBox::title {{
    background-color: {island};
}}

/* No focus rectangle around the current cell/row (the border Qt draws on
   the focused item, separate from the selection highlight). */
QTreeView,
QListView,
QTableView {{
    outline: none;
}}

/* Item views: no border (the surface card / panel provides the framing). */
QAbstractItemView {{
    border: none;
}}

/* Square rows/cells — the islands themes round item cells by default.
   Covers tree/list/table views (and their *Widget subclasses). */
QTreeView::item,
QListView::item,
QTableView::item {{
    border-radius: 0px;
}}

/* List rows: horizontal-only padding. The themes' 4px all-around item
   padding pushes QListView/QListWidget text below the icon's vertical center
   (a QSS list-mode quirk; tree/table views are unaffected and keep theirs). */
QListView::item {{
    padding: 0px 4px;
}}

/* Context menus popped from panel widgets are parented (QObject-wise) inside
   the proxy, so the '#TheaterProxy QWidget' panel-transparency rule above
   matches them and blanks their background. Re-assert the sea background —
   same specificity, placed later, so it wins. */
#TheaterProxy QMenu {{
    background-color: {sea};
}}

/* Submenus (e.g. View > Panels > ...) are QMenus parented to their parent
   QMenu, so the themes' 'QMenu QWidget {{ background: transparent }}' corner-
   fix rule matches them and blanks their background. Re-assert sea for nested
   menus (same specificity, emitted later, so it wins). */
QMenu QMenu {{
    background-color: {sea};
}}

/* Stacked widgets sit on surface. */
QStackedWidget {{
    background-color: {island};
}}

/* Dialogs sit on surface. */
QDialog {{
    background-color: {island};
}}

/* Push buttons: flat, sea-colored, borderless. */
QPushButton {{
    background: {sea};
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 4px 12px;
}}
QPushButton:hover {{
    background: {colors.border};
}}
QPushButton:pressed {{
    background: {colors.text_secondary};
}}
QPushButton:disabled {{
    background: {sea};
    color: {colors.text_secondary};
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
/* SHELL: the central widget (e.g. logbook) — rounded surface card. Its
   interior (scroll area + content) is container, made transparent by the
   global rules above, so this rounded surface shows through. */
#InnerDockWindow > QWidget {{
    background-color: {island};
    border-radius: {radius}px;
    margin: {gap}px;
}}
'''}

/* --------------------------------------------------------------------------
   Splitters — island colored with rounded corners
   -------------------------------------------------------------------------- */
{"" if not islands else f'''
QSplitter {{
    background-color: {island};
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
