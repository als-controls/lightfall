"""QtAds theme integration.

Generates stylesheets for QtAds widgets that match the application theme.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lucid.ui.theme.manager import ThemeColors


def generate_qtads_stylesheet(colors: ThemeColors) -> str:
    """Generate a stylesheet for QtAds widgets.

    Args:
        colors: The current theme colors.

    Returns:
        CSS stylesheet string for QtAds widgets.
    """
    return f"""
/* ==========================================================================
   Icon Strip Sidebar
   ========================================================================== */
#IconStripSidebar {{
    background: {colors.surface};
    border-right: 1px solid {colors.border};
}}

#IconStripSidebar QToolButton {{
    border: none;
    border-radius: 4px;
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
    background: {colors.surface};
    border-bottom: 1px solid {colors.border};
}}

#PanelTitleLabel {{
    color: {colors.text};
    font-weight: 500;
    font-size: 12px;
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
   Auto-hide sidebar tabs (icon buttons on the edges)
   Large icons, no rotation, tooltip on hover
   -------------------------------------------------------------------------- */
ads--CAutoHideTab {{
    background: {colors.surface};
    border: 1px solid {colors.border};
    padding: 3px;
    qproperty-iconSize: 17px 17px;
    /* Prevent icon rotation on vertical sidebars */
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
    background: {colors.background};
    border: 1px solid {colors.border};
}}

/* --------------------------------------------------------------------------
   Auto-hide sidebar (the icon strip itself)
   -------------------------------------------------------------------------- */
ads--CAutoHideSideBar {{
    background: {colors.surface};
    border: none;
}}

/* --------------------------------------------------------------------------
   Dock widget tabs (styled as flat title headers, not clickable tabs)
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
   Dock area title bar (contains tab bar and buttons)
   Styled as a minimal header bar
   -------------------------------------------------------------------------- */
ads--CDockAreaTitleBar {{
    background: {colors.surface};
    border: none;
    border-bottom: 1px solid {colors.border};
    padding: 0;
    min-height: 26px;
    max-height: 26px;
}}

/* Title bar buttons (close, menu, etc.) */
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
   Dock area tab bar (holds the tabs/titles)
   -------------------------------------------------------------------------- */
ads--CDockAreaTabBar {{
    background: {colors.surface};
    border: none;
}}

/* --------------------------------------------------------------------------
   Dock splitters
   -------------------------------------------------------------------------- */
ads--CDockSplitter::handle {{
    background: {colors.border};
}}

ads--CDockSplitter::handle:horizontal {{
    width: 2px;
}}

ads--CDockSplitter::handle:vertical {{
    height: 2px;
}}

ads--CDockSplitter::handle:hover {{
    background: {colors.primary};
}}

/* --------------------------------------------------------------------------
   Dock container (main dock area)
   -------------------------------------------------------------------------- */
ads--CDockContainerWidget {{
    background: {colors.background};
}}

/* --------------------------------------------------------------------------
   Dock area widget
   -------------------------------------------------------------------------- */
ads--CDockAreaWidget {{
    background: {colors.background};
    border: 1px solid {colors.border};
}}

ads--CDockAreaWidget[focused="true"] {{
    border-color: {colors.primary};
}}

/* --------------------------------------------------------------------------
   Floating dock container (detached windows)
   -------------------------------------------------------------------------- */
ads--CFloatingDockContainer {{
    background: {colors.background};
    border: 1px solid {colors.border};
}}

/* Floating window title bar */
ads--CFloatingWidgetTitleBar {{
    background: {colors.surface};
    border-bottom: 1px solid {colors.border};
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
    qproperty-iconColors: "{colors.background} {colors.primary} {colors.text} {colors.border} {colors.surface}";
}}

/* --------------------------------------------------------------------------
   Resize handle
   -------------------------------------------------------------------------- */
ads--CResizeHandle {{
    background: {colors.border};
}}

ads--CResizeHandle:hover {{
    background: {colors.primary};
}}
"""
