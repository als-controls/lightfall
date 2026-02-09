"""SidebarManager - Manages PyCharm-like icon strip sidebars.

Provides grouped auto-hide sidebars on the left and bottom edges
of the dock manager with icon-based panel activation.

Architecture:
- Left sidebar (SideBarLeft): Primary tools like Bluesky, Devices
- Bottom sidebar (SideBarBottom): Auxiliary panels like Claude, Documents, Logging, Synoptic

Auto-hide panels use reasonable default sizes when expanded:
- Left panels: 350px wide
- Bottom panels: 300px tall

Only one sidebar panel can be expanded at a time - opening a new panel
automatically collapses any previously open panel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6QtAds import CDockManager, SideBarBottom, SideBarLeft, SideBarRight

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6QtAds import CAutoHideDockContainer

    from lucid.ui.docking.widget import PanelDockWidget


# Map sidebar group names to QtAds SideBarLocation values
SIDEBAR_LOCATIONS = {
    "left_top": SideBarLeft,
    "left_bottom": SideBarLeft,
    "right_top": SideBarRight,
    "right_bottom": SideBarRight,
    "bottom_top": SideBarBottom,
    "bottom_bottom": SideBarBottom,
    "bottom": SideBarBottom,
}

# Default sizes for auto-hide panels (in pixels)
# Left sidebar panels slide out horizontally, so this is width
AUTOHIDE_LEFT_SIZE = 350
# Bottom sidebar panels slide up vertically, so this is height
AUTOHIDE_BOTTOM_SIZE = 300


class SidebarManager:
    """Manages PyCharm-like icon strip sidebars for a CDockManager.

    Handles multiple sidebar groups:
    - left_top, left_bottom: Groups on the left edge (SideBarLeft)
    - bottom_top, bottom_bottom: Groups on the bottom edge (SideBarBottom)

    Each group appears as a strip of icons that, when clicked,
    slide out the corresponding panel.

    Only one sidebar panel can be open at a time - this mimics IDE behavior
    where opening one tool window collapses any other open sidebar.
    """

    def __init__(self, dock_manager: CDockManager) -> None:
        """Initialize the sidebar manager.

        Args:
            dock_manager: The CDockManager instance this manager controls.
        """
        self._dock_manager = dock_manager
        self._group_panels: dict[str, list[str]] = {
            "left_top": [],
            "left_bottom": [],
            "right_top": [],
            "right_bottom": [],
            "bottom_top": [],
            "bottom_bottom": [],
            "bottom": [],
        }
        self._panel_widgets: dict[str, PanelDockWidget] = {}
        self._auto_hide_containers: dict[str, CAutoHideDockContainer] = {}
        self._handling_view_toggle = False  # Prevent recursive collapse

    def add_panel(
        self,
        widget: PanelDockWidget,
        group: str,
        sidebar_location: int | None = None,
        *,
        insert_index: int = -1,
        size: int | None = None,
    ) -> None:
        """Add a panel to a sidebar group.

        Args:
            widget: The PanelDockWidget to add.
            group: Sidebar group name (e.g., "left_top", "bottom_top").
            sidebar_location: SideBarLocation value (SideBarLeft, SideBarBottom, etc.).
                If None, determined from group name.
            insert_index: Position within the sidebar (-1 for end).
            size: Panel size in pixels when expanded. If None, uses default
                based on sidebar location (AUTOHIDE_LEFT_SIZE or AUTOHIDE_BOTTOM_SIZE).
        """
        # Use explicit location if provided, otherwise infer from group name
        if sidebar_location is not None:
            location = sidebar_location
        else:
            location = SIDEBAR_LOCATIONS.get(group, SideBarLeft)

        # Add to auto-hide sidebar
        auto_hide_container = self._dock_manager.addAutoHideDockWidget(
            location, widget
        )

        if auto_hide_container is None:
            logger.warning(
                "Failed to add {} to auto-hide sidebar {}",
                widget.panel_id,
                group,
            )
            return

        # Set panel size based on sidebar location
        if size is not None:
            panel_size = size
        elif location == SideBarLeft:
            panel_size = AUTOHIDE_LEFT_SIZE
        else:
            panel_size = AUTOHIDE_BOTTOM_SIZE

        auto_hide_container.setSize(panel_size)

        # Track the panel and its container
        panel_id = widget.panel_id
        if group not in self._group_panels:
            self._group_panels[group] = []
        self._group_panels[group].append(panel_id)
        self._panel_widgets[panel_id] = widget
        self._auto_hide_containers[panel_id] = auto_hide_container

        # Connect to view toggle signal to implement exclusive behavior
        widget.viewToggled.connect(
            lambda visible, pid=panel_id: self._on_panel_view_toggled(pid, visible)
        )

        logger.debug("Added {} to sidebar group {} (size={})", panel_id, group, panel_size)

    def _on_panel_view_toggled(self, panel_id: str, visible: bool) -> None:
        """Handle panel view toggle - collapse other panels when one opens.

        Args:
            panel_id: The panel that was toggled.
            visible: Whether the panel is now visible.
        """
        if not visible or self._handling_view_toggle:
            return

        # Prevent recursive handling
        self._handling_view_toggle = True
        try:
            # Collapse all other auto-hide containers
            for other_id, container in self._auto_hide_containers.items():
                if other_id != panel_id:
                    # Only collapse if it's currently expanded
                    if hasattr(container, 'collapseView'):
                        container.collapseView(True)
            logger.debug("Collapsed other sidebars, {} is now active", panel_id)
        finally:
            self._handling_view_toggle = False

    def collapse_all(self) -> None:
        """Collapse all open sidebar panels."""
        for container in self._auto_hide_containers.values():
            if hasattr(container, 'collapseView'):
                container.collapseView(True)

    def remove_panel(self, panel_id: str) -> bool:
        """Remove a panel from sidebar management.

        Args:
            panel_id: The panel identifier.

        Returns:
            True if panel was removed.
        """
        if panel_id not in self._panel_widgets:
            return False

        # Remove from group tracking
        for panels in self._group_panels.values():
            if panel_id in panels:
                panels.remove(panel_id)
                break

        # Remove from tracking dicts
        del self._panel_widgets[panel_id]
        self._auto_hide_containers.pop(panel_id, None)

        return True

    def get_group(self, panel_id: str) -> str | None:
        """Get the sidebar group for a panel.

        Args:
            panel_id: The panel identifier.

        Returns:
            Group name or None if not in a sidebar.
        """
        for group, panels in self._group_panels.items():
            if panel_id in panels:
                return group
        return None

    def get_panels_in_group(self, group: str) -> list[str]:
        """Get all panel IDs in a sidebar group.

        Args:
            group: Sidebar group name.

        Returns:
            List of panel IDs.
        """
        return list(self._group_panels.get(group, []))

    def is_panel_in_sidebar(self, panel_id: str) -> bool:
        """Check if a panel is in any sidebar.

        Args:
            panel_id: The panel identifier.

        Returns:
            True if panel is in a sidebar.
        """
        return panel_id in self._panel_widgets

    def get_active_panel(self) -> str | None:
        """Get the currently expanded sidebar panel, if any.

        Returns:
            Panel ID of the expanded panel, or None if all collapsed.
        """
        for panel_id, _container in self._auto_hide_containers.items():
            # Check if container is expanded (visible but not collapsed)
            widget = self._panel_widgets.get(panel_id)
            if widget and not widget.isClosed():
                return panel_id
        return None
