"""QtAds theme integration.

Generates stylesheets for QtAds widgets that match the application theme.

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
TITLE_HEIGHT = 30    # dock title bar height


def _is_islands_mode(colors: ThemeColors) -> bool:
    """Check whether the current theme uses Islands layout.

    Islands mode is active when the theme defines a ``sea`` color that
    differs from ``background``.
    """
    return bool(colors.sea) and colors.sea != colors.background


def generate_qtads_stylesheet(colors: ThemeColors) -> str:
    """Generate a stylesheet for QtAds widgets.

    Args:
        colors: The current theme colors.

    Returns:
        CSS stylesheet string for QtAds widgets.
    """
    islands = _is_islands_mode(colors)
    sea = colors.sea if islands else colors.background
    island = colors.surface if islands else colors.surface

    # In Islands mode the dock manager background is the sea.
    # Panels (islands) float on it with rounded corners and no borders.
    # In non-Islands mode we fall back to the existing flat look.

    border_rule = "border: none;" if islands else f"border: 1px solid {colors.border};"
    dock_area_border = "border: none;" if islands else f"border: 1px solid {colors.border};"
    radius = RADIUS if islands else 0

    return f"""
/* ==========================================================================
   Icon Strip Sidebar
   ========================================================================== */
#IconStripSidebar {{
    background: {island if islands else colors.surface};
    border-right: {"none" if islands else f"1px solid {colors.border}"};
    {"border-top-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-bottom-left-radius: " + str(radius) + "px;" if islands else ""}
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
   Custom Panel Title Bar (for side panels with NoTab)
   -------------------------------------------------------------------------- */
#PanelTitleBar {{
    background: {island};
    border-bottom: 1px solid {colors.border};
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
   QtAds Advanced Docking Stylesheet
   ========================================================================== */

/* --------------------------------------------------------------------------
   Dock manager — the "sea"
   -------------------------------------------------------------------------- */
ads--CDockManager {{
    background: {sea};
    {"padding: " + str(GAP) + "px;" if islands else ""}
}}

/* --------------------------------------------------------------------------
   Auto-hide sidebar tabs (icon buttons on the edges)
   -------------------------------------------------------------------------- */
ads--CAutoHideTab {{
    background: {island};
    border: 1px solid {colors.border};
    padding: 3px;
    qproperty-iconSize: 17px 17px;
    qproperty-iconRotated: false;
}}

ads--CAutoHideTab:hover {{
    background: {colors.border};
}}

ads--CAutoHideTab[activeTab="true"] {{
    background: {colors.primary};
    border-color: {colors.primary};
}}

/* --------------------------------------------------------------------------
   Auto-hide sidebar container (the slide-out panel)
   -------------------------------------------------------------------------- */
ads--CAutoHideDockContainer {{
    background: {island};
    {border_rule}
    {"border-radius: " + str(radius) + "px;" if islands else ""}
}}

/* --------------------------------------------------------------------------
   Auto-hide sidebar (the icon strip itself)
   -------------------------------------------------------------------------- */
ads--CAutoHideSideBar {{
    background: {island if islands else colors.surface};
    border: none;
}}

/* --------------------------------------------------------------------------
   Dock widget tabs
   -------------------------------------------------------------------------- */
ads--CDockWidgetTab {{
    background: transparent;
    border: none;
    padding: 4px 8px;
    margin: 0;
}}

ads--CDockWidgetTab:hover {{
    background: transparent;
}}

ads--CDockWidgetTab[activeTab="true"] {{
    background: transparent;
}}

ads--CDockWidgetTab[focused="true"] {{
    background: transparent;
}}

/* Tab/title label */
ads--CDockWidgetTab > QLabel {{
    color: {colors.text};
    font-weight: 500;
    font-size: 12px;
}}

/* Tab buttons (close, etc.) */
ads--CDockWidgetTab QToolButton {{
    background: transparent;
    border: none;
    padding: 2px;
}}

ads--CDockWidgetTab QToolButton:hover {{
    background: {colors.border};
    border-radius: 2px;
}}

/* --------------------------------------------------------------------------
   Dock area title bar
   -------------------------------------------------------------------------- */
ads--CDockAreaTitleBar {{
    background: {island};
    border: none;
    border-bottom: 1px solid {colors.border};
    padding: 0;
    min-height: {TITLE_HEIGHT}px;
    max-height: {TITLE_HEIGHT}px;
    {"border-top-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-top-right-radius: " + str(radius) + "px;" if islands else ""}
}}

/* Title bar buttons */
ads--CDockAreaTitleBar QToolButton {{
    background: transparent;
    border: none;
    padding: 4px;
    margin: 2px;
}}

ads--CDockAreaTitleBar QToolButton:hover {{
    background: {colors.border};
    border-radius: 2px;
}}

ads--CDockAreaTitleBar QToolButton:pressed {{
    background: {colors.text_secondary};
}}

/* --------------------------------------------------------------------------
   Dock area tab bar
   -------------------------------------------------------------------------- */
ads--CDockAreaTabBar {{
    background: {island};
    border: none;
}}

/* --------------------------------------------------------------------------
   Dock splitters — sea-colored gaps between islands
   -------------------------------------------------------------------------- */
ads--CDockSplitter {{
    background: {sea};
}}

ads--CDockSplitter::handle {{
    background: {sea};
}}

ads--CDockSplitter::handle:horizontal {{
    width: {GAP if islands else 2}px;
}}

ads--CDockSplitter::handle:vertical {{
    height: {GAP if islands else 2}px;
}}

ads--CDockSplitter::handle:hover {{
    background: {colors.primary};
}}

/* --------------------------------------------------------------------------
   Dock container (main dock area)
   -------------------------------------------------------------------------- */
ads--CDockContainerWidget {{
    background: {sea};
}}

/* --------------------------------------------------------------------------
   Dock area widget — each "island"
   -------------------------------------------------------------------------- */
ads--CDockAreaWidget {{
    background: {island};
    {dock_area_border}
    {"border-radius: " + str(radius) + "px;" if islands else ""}
}}

ads--CDockAreaWidget[focused="true"] {{
    {"border: 1px solid " + colors.primary + ";" if not islands else ""}
    {"" if not islands else "/* focus indicated by accent, no border in islands */"}
}}

/* --------------------------------------------------------------------------
   Content inside dock areas — match island rounding
   -------------------------------------------------------------------------- */
{"" if not islands else f"""
ads--CDockAreaWidget > QWidget {{
    background: {island};
    border-bottom-left-radius: {radius}px;
    border-bottom-right-radius: {radius}px;
}}

/* Scrollable content widgets inside dock areas inherit rounding */
ads--CDockAreaWidget QPlainTextEdit,
ads--CDockAreaWidget QTextEdit,
ads--CDockAreaWidget QListView,
ads--CDockAreaWidget QTreeView,
ads--CDockAreaWidget QTableView {{
    border: none;
    border-radius: 0px;
    border-bottom-left-radius: {radius}px;
    border-bottom-right-radius: {radius}px;
    background: {island};
}}
"""}

/* --------------------------------------------------------------------------
   Floating dock container (detached windows)
   -------------------------------------------------------------------------- */
ads--CFloatingDockContainer {{
    background: {island};
    {border_rule}
    {"border-radius: " + str(radius) + "px;" if islands else ""}
}}

/* Floating window title bar */
ads--CFloatingWidgetTitleBar {{
    background: {island};
    border-bottom: 1px solid {colors.border};
    {"border-top-left-radius: " + str(radius) + "px;" if islands else ""}
    {"border-top-right-radius: " + str(radius) + "px;" if islands else ""}
}}

ads--CFloatingWidgetTitleBar QLabel {{
    color: {colors.text};
    padding-left: 4px;
}}

ads--CFloatingWidgetTitleBar QToolButton {{
    background: transparent;
    border: none;
}}

ads--CFloatingWidgetTitleBar QToolButton:hover {{
    background: {colors.border};
    border-radius: 2px;
}}

/* --------------------------------------------------------------------------
   Overlay cross for drop targets
   -------------------------------------------------------------------------- */
ads--CDockOverlayCross {{
    qproperty-iconColors: "{island} {colors.primary} {colors.text} {colors.border} {island}";
}}

/* --------------------------------------------------------------------------
   Resize handle
   -------------------------------------------------------------------------- */
ads--CResizeHandle {{
    background: {sea if islands else colors.border};
}}

ads--CResizeHandle:hover {{
    background: {colors.primary};
}}
"""
