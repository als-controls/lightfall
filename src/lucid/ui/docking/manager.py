"""DockingManager - Central manager for the advanced docking system.

Wraps CDockManager from PySide6-QtAds to provide:
- Single CDockManager with custom icon strip sidebar
- Panel management with exclusive visibility per area
- Logbook always visible in center
- Layout state persistence
- Theme integration

Architecture:
    NCSMainWindow
    └── QHBoxLayout
        ├── IconStripSidebar (custom icon strip)
        └── CDockManager
            ├── Left dock area (Bluesky, Devices - one at a time)
            ├── Center dock area (Logbook, always visible)
            └── Bottom dock area (Claude, Documents, etc. - one at a time)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QObject, QSettings, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget
from PySide6QtAds import (
    BottomDockWidgetArea,
    CDockManager,
    CDockWidget,
    CenterDockWidgetArea,
    LeftDockWidgetArea,
    RightDockWidgetArea,
    TopDockWidgetArea,
)

from lucid.ui.docking.icon_sidebar import IconStripSidebar
from lucid.ui.docking.state import DockingState
from lucid.ui.docking.widget import PanelDockWidget
from lucid.utils.logging import logger

# Default sizes for side panels (in pixels)
LEFT_PANEL_WIDTH = 350
BOTTOM_PANEL_HEIGHT = 250

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow

    from lucid.ui.panels.base import BasePanel


# Map area names to DockWidgetArea enum
AREA_MAP = {
    "left": LeftDockWidgetArea,
    "bottom": BottomDockWidgetArea,
    "center": CenterDockWidgetArea,
}

# Reverse map: DockWidgetArea enum to area name
AREA_NAME_MAP = {
    LeftDockWidgetArea: "left",
    RightDockWidgetArea: "left",  # Treat right as left for sidebar purposes
    BottomDockWidgetArea: "bottom",
    TopDockWidgetArea: "bottom",  # Treat top as bottom for sidebar purposes
    CenterDockWidgetArea: "center",
}


class DockingManager(QObject):
    """Manages the advanced docking system using PySide6-QtAds.

    Uses a single CDockManager with custom icon strip sidebar:
    - Left dock area for primary tools (Bluesky, Devices) - one visible at a time
    - Bottom dock area for auxiliary panels (Claude, Documents, etc.) - one visible at a time
    - Center area for always-visible panels (Logbook)

    The icon strip sidebar provides VS Code/PyCharm-like navigation where
    icons remain visible regardless of panel state.

    Signals:
        panel_added: Emitted when a panel is added (panel_id).
        panel_removed: Emitted when a panel is removed (panel_id).
        panel_focused: Emitted when a panel gains focus (panel_id).
        layout_changed: Emitted when the dock layout changes.
    """

    panel_added = Signal(str)
    panel_removed = Signal(str)
    panel_focused = Signal(str)
    layout_changed = Signal()

    def __init__(self, main_window: QMainWindow, parent: QObject | None = None) -> None:
        """Initialize the docking manager.

        Args:
            main_window: The main window to attach docking to.
            parent: Optional parent object.
        """
        super().__init__(parent)
        self._main_window = main_window
        self._dock_manager: CDockManager | None = None
        self._icon_sidebar: IconStripSidebar | None = None
        self._panel_widgets: dict[str, PanelDockWidget] = {}
        self._panel_areas: dict[str, str] = {}  # panel_id -> "left", "bottom", "center"
        self._state_manager: DockingState | None = None
        self._active_panel_id: str | None = None
        self._central_widget: QWidget | None = None
        self._center_dock_area = None  # Track center area for relative positioning

        # Zone tracking: track CDockAreaWidget objects by zone for reliable area detection
        self._left_dock_areas: set = set()    # CDockAreaWidget objects in left zone
        self._bottom_dock_areas: set = set()  # CDockAreaWidget objects in bottom zone
        self._right_dock_areas: set = set()   # CDockAreaWidget objects in right zone

    def initialize(self) -> None:
        """Initialize CDockManager with custom icon strip sidebar.

        Creates:
        - Central widget with horizontal layout
        - IconStripSidebar on the left
        - CDockManager for dock areas

        Must be called after the main window is created but before
        adding any panels.
        """
        # Configure CDockManager global options
        CDockManager.setConfigFlag(CDockManager.OpaqueSplitterResize, True)
        CDockManager.setConfigFlag(CDockManager.FocusHighlighting, True)
        CDockManager.setConfigFlag(CDockManager.DockAreaHasTabsMenuButton, False)
        CDockManager.setConfigFlag(CDockManager.DockAreaHasUndockButton, False)
        CDockManager.setConfigFlag(CDockManager.HideSingleCentralWidgetTitleBar, True)

        # Create central widget with horizontal layout
        self._central_widget = QWidget()
        layout = QHBoxLayout(self._central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create icon strip sidebar
        self._icon_sidebar = IconStripSidebar()
        self._icon_sidebar.panel_toggled.connect(self._on_sidebar_panel_toggled)
        self._icon_sidebar.panel_section_changed.connect(self._on_sidebar_section_changed)
        layout.addWidget(self._icon_sidebar)

        # Create dock manager
        self._dock_manager = CDockManager()
        self._dock_manager.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._dock_manager)

        # Set as main window central widget
        self._main_window.setCentralWidget(self._central_widget)

        # Create state manager
        self._state_manager = DockingState(self._dock_manager)

        # Connect focus tracking
        self._dock_manager.focusedDockWidgetChanged.connect(self._on_focus_changed)

        logger.info("DockingManager initialized with icon strip sidebar")

    @property
    def dock_manager(self) -> CDockManager | None:
        """Get the CDockManager instance."""
        return self._dock_manager

    @property
    def icon_sidebar(self) -> IconStripSidebar | None:
        """Get the icon strip sidebar."""
        return self._icon_sidebar

    def add_panel(
        self,
        panel_id: str,
        panel: BasePanel,
        *,
        area: str | None = None,
        add_sidebar_button: bool = True,
    ) -> PanelDockWidget | None:
        """Add a panel to the docking system.

        Routes panels to appropriate locations based on area:
        - "left" → Left dock area (exclusive, one at a time)
        - "bottom" → Bottom dock area (exclusive, one at a time)
        - "center" → Center dock area (always visible)

        Args:
            panel_id: Unique panel identifier.
            panel: The BasePanel instance.
            area: Dock area ("left", "bottom", "center").
                Defaults to panel's default_area metadata.
            add_sidebar_button: Whether to add sidebar button immediately.
                Set False to defer button creation for layout ordering.

        Returns:
            The created PanelDockWidget or None on failure.
        """
        if self._dock_manager is None:
            logger.error("DockingManager not initialized")
            return None

        if panel_id in self._panel_widgets:
            # Panel already exists, just show and focus it
            widget = self._panel_widgets[panel_id]
            self._show_panel_exclusive(panel_id)
            return widget

        # Determine area from metadata if not specified
        if area is None:
            area = panel.panel_metadata.default_area

        # Create dock widget - side panels use custom title bar
        use_custom_title = area in ("left", "bottom")
        widget = PanelDockWidget(panel, use_custom_title_bar=use_custom_title)
        self._panel_widgets[panel_id] = widget

        # Store the area for this panel
        self._panel_areas[panel_id] = area

        # Get dock widget area
        dock_area = AREA_MAP.get(area, CenterDockWidgetArea)

        # Configure widget based on area
        if area in ("left", "bottom"):
            # Side panels: use custom title bar, hide QtAds title bar
            # Set NoTab so there's no tab appearance
            widget.setFeature(CDockWidget.NoTab, True)

            # Set size hints for proper initial sizing
            if area == "left":
                widget.setMinimumWidth(LEFT_PANEL_WIDTH)
                panel.setMinimumWidth(LEFT_PANEL_WIDTH)
            else:  # bottom
                widget.setMinimumHeight(BOTTOM_PANEL_HEIGHT)
                panel.setMinimumHeight(BOTTOM_PANEL_HEIGHT)

            # Add to dock manager
            # For left panels, add relative to center so bottom spans full width
            if area == "left" and self._center_dock_area is not None:
                dock_area_widget = self._dock_manager.addDockWidget(
                    dock_area, widget, self._center_dock_area
                )
            else:
                dock_area_widget = self._dock_manager.addDockWidget(dock_area, widget)

            # Hide the QtAds title bar for side panels - we use our custom one
            if dock_area_widget is not None:
                title_bar = dock_area_widget.titleBar()
                if title_bar is not None:
                    title_bar.setVisible(False)

            # Register dock area in the appropriate zone for reliable area detection
            if dock_area_widget is not None:
                self._register_dock_area(dock_area_widget, area)

            # Initially hidden (user clicks sidebar to show)
            widget.toggleView(False)
            # Add button to sidebar (unless deferred for layout ordering)
            if add_sidebar_button and self._icon_sidebar:
                self._icon_sidebar.add_panel_button(
                    panel_id,
                    panel.panel_metadata.icon,
                    panel.panel_metadata.name,
                )
        else:
            # Center panel: no title bar needed, always visible
            widget.setFeature(CDockWidget.NoTab, True)
            dock_area_widget = self._dock_manager.addDockWidget(dock_area, widget)
            # Store center dock area for relative positioning of left panels
            # This ensures bottom area spans full width (not just center+bottom column)
            if self._center_dock_area is None:
                self._center_dock_area = dock_area_widget
            # Hide the title bar for center panels too
            if dock_area_widget is not None:
                title_bar = dock_area_widget.titleBar()
                if title_bar is not None:
                    title_bar.setVisible(False)

        # Connect visibility changes to sync sidebar
        widget.viewToggled.connect(
            lambda visible, pid=panel_id: self._on_panel_visibility_changed(pid, visible)
        )

        # Connect to dock area change signal for sidebar sync when dragging
        widget.dock_area_changed.connect(
            lambda pid=panel_id: self._sync_panel_area(pid)
        )

        # Connect to topLevelChanged to detect when panel is dropped after dragging
        if area in ("left", "bottom"):
            widget.topLevelChanged.connect(
                lambda floating, pid=panel_id: self._on_panel_top_level_changed(pid, floating)
            )

        logger.debug("Added panel {} to area {}", panel_id, area)
        self.panel_added.emit(panel_id)

        return widget

    def add_sidebar_stretch(self) -> None:
        """Add stretch to sidebar to separate top and bottom icons."""
        if self._icon_sidebar:
            self._icon_sidebar.add_stretch()

    def add_sidebar_button(self, panel_id: str) -> bool:
        """Add a sidebar button for an existing panel.

        Use this after adding a panel with add_sidebar_button=False
        to control the order of sidebar icons independently of dock layout.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if button was added.
        """
        if self._icon_sidebar is None:
            return False

        widget = self._panel_widgets.get(panel_id)
        if widget is None:
            return False

        panel = widget.panel
        self._icon_sidebar.add_panel_button(
            panel_id,
            panel.panel_metadata.icon,
            panel.panel_metadata.name,
        )
        return True

    def remove_panel(self, panel_id: str) -> bool:
        """Remove a panel from the docking system.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if panel was removed.
        """
        widget = self._panel_widgets.pop(panel_id, None)
        if widget is None:
            return False

        # Remove area tracking
        self._panel_areas.pop(panel_id, None)

        # Close and delete the dock widget
        widget.closeDockWidget()

        logger.debug("Removed panel {} from docking system", panel_id)
        self.panel_removed.emit(panel_id)

        return True

    def get_panel(self, panel_id: str) -> BasePanel | None:
        """Get a panel by ID.

        Args:
            panel_id: Panel identifier.

        Returns:
            The BasePanel instance or None.
        """
        widget = self._panel_widgets.get(panel_id)
        if widget:
            return widget.panel
        return None

    def get_dock_widget(self, panel_id: str) -> PanelDockWidget | None:
        """Get a dock widget by panel ID.

        Args:
            panel_id: Panel identifier.

        Returns:
            The PanelDockWidget or None.
        """
        return self._panel_widgets.get(panel_id)

    def list_panels(self) -> list[str]:
        """Get list of all panel IDs.

        Returns:
            List of panel identifiers.
        """
        return list(self._panel_widgets.keys())

    def toggle_panel(self, panel_id: str) -> bool:
        """Toggle panel visibility.

        For side panels (left/bottom), implements exclusive behavior.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if toggle was successful.
        """
        widget = self._panel_widgets.get(panel_id)
        if widget is None:
            return False

        if widget.isClosed():
            self._show_panel_exclusive(panel_id)
        else:
            widget.toggleView(False)

        return True

    def show_panel(self, panel_id: str) -> bool:
        """Show and focus a panel.

        For side panels, hides other panels in the same area first.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if successful.
        """
        return self._show_panel_exclusive(panel_id)

    def hide_panel(self, panel_id: str) -> bool:
        """Hide a panel.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if successful.
        """
        widget = self._panel_widgets.get(panel_id)
        if widget is None:
            return False

        widget.toggleView(False)
        return True

    def _show_panel_exclusive(self, panel_id: str) -> bool:
        """Show a panel, hiding others in the same area.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if successful.
        """
        widget = self._panel_widgets.get(panel_id)
        if widget is None:
            return False

        panel_area = self._panel_areas.get(panel_id)

        # For side panels, hide others in the same area first
        if panel_area in ("left", "bottom"):
            for other_id, other_area in self._panel_areas.items():
                if other_id != panel_id and other_area == panel_area:
                    other_widget = self._panel_widgets.get(other_id)
                    if other_widget and not other_widget.isClosed():
                        other_widget.toggleView(False)

        # Show the panel
        widget.toggleView(True)
        widget.raise_()

        # Ensure all dock area title bars are hidden
        # (they may have been recreated/shown when panels change)
        self._hide_all_title_bars()

        if self._dock_manager:
            self._dock_manager.setDockWidgetFocused(widget)

        return True

    def _hide_all_title_bars(self) -> None:
        """Hide all dock area title bars.

        Called after panel visibility changes to ensure title bars
        stay hidden (QtAds may recreate/show them during layout changes).
        """
        for widget in self._panel_widgets.values():
            if not widget.isClosed():
                dock_area_widget = widget.dockAreaWidget()
                if dock_area_widget is not None:
                    title_bar = dock_area_widget.titleBar()
                    if title_bar is not None:
                        title_bar.setVisible(False)

    def _on_sidebar_panel_toggled(self, panel_id: str, should_show: bool) -> None:
        """Handle sidebar button toggle.

        Args:
            panel_id: The panel that was toggled.
            should_show: Whether to show or hide the panel.
        """
        if should_show:
            self._show_panel_exclusive(panel_id)
        else:
            self.hide_panel(panel_id)

    def _on_sidebar_section_changed(self, panel_id: str, new_section: str) -> None:
        """Move a panel to a different dock area when its sidebar icon changes section.

        This is the reverse of _sync_panel_area(): when a user drags a sidebar
        icon from the top section to the bottom (or vice versa), we move the
        actual panel to the corresponding dock area.

        Args:
            panel_id: Panel identifier.
            new_section: Target section ("top" = left dock, "bottom" = bottom dock).
        """
        widget = self._panel_widgets.get(panel_id)
        if widget is None or self._dock_manager is None:
            return

        # Map section to dock area
        target_area = "left" if new_section == "top" else "bottom"
        current_area = self._panel_areas.get(panel_id)

        if current_area == target_area:
            return  # Already in correct area

        # Move the panel to the new dock area
        self._move_panel_to_area(panel_id, target_area)

    def _move_panel_to_area(self, panel_id: str, target_area: str) -> None:
        """Move a panel to a different dock area.

        Args:
            panel_id: Panel identifier.
            target_area: Target area ("left" or "bottom").
        """
        widget = self._panel_widgets.get(panel_id)
        if widget is None or self._dock_manager is None:
            return

        # Remember visibility state
        was_visible = not widget.isClosed()

        # Get target dock area enum
        dock_area = AREA_MAP.get(target_area)
        if dock_area is None:
            return

        # Re-dock the widget to the new area
        # For left panels, add relative to center so bottom spans full width
        if target_area == "left" and self._center_dock_area is not None:
            dock_area_widget = self._dock_manager.addDockWidget(
                dock_area, widget, self._center_dock_area
            )
        else:
            dock_area_widget = self._dock_manager.addDockWidget(dock_area, widget)

        # Register the new dock area in the zone set
        if dock_area_widget is not None:
            self._register_dock_area(dock_area_widget, target_area)

        # Update internal tracking
        old_area = self._panel_areas.get(panel_id)
        self._panel_areas[panel_id] = target_area

        # Hide the title bar (we use custom title bars for side panels)
        if dock_area_widget is not None:
            title_bar = dock_area_widget.titleBar()
            if title_bar is not None:
                title_bar.setVisible(False)

        # Restore visibility (or handle exclusive visibility in new area)
        if was_visible:
            # Enforce exclusive visibility in the new area
            for other_id, other_area in self._panel_areas.items():
                if other_id != panel_id and other_area == target_area:
                    other_widget = self._panel_widgets.get(other_id)
                    if other_widget and not other_widget.isClosed():
                        other_widget.toggleView(False)
            widget.toggleView(True)

        logger.info("Moved panel {} from {} to {} via sidebar drag", panel_id, old_area, target_area)

    def _on_panel_visibility_changed(self, panel_id: str, visible: bool) -> None:
        """Handle panel visibility change (e.g., closed via X button).

        Keeps the sidebar in sync with actual panel state, and moves
        sidebar icons if the panel has been dragged to a different area.

        Args:
            panel_id: The panel whose visibility changed.
            visible: Whether the panel is now visible.
        """
        if self._icon_sidebar:
            self._icon_sidebar.set_panel_active(panel_id, visible)

        # Check if panel area has changed (e.g., dragged from left to bottom)
        if visible:
            self._sync_panel_area(panel_id)

    def _on_panel_top_level_changed(self, panel_id: str, floating: bool) -> None:
        """Handle panel floating state change.

        When a panel is dropped (floating becomes False), check if it
        landed in a different dock area and sync the sidebar icon.

        Args:
            panel_id: Panel identifier.
            floating: True if panel is now floating, False if docked.
        """
        if floating:
            return  # Only care about when panel is dropped (docked)

        # Panel was just dropped - sync its area
        self._sync_panel_area(panel_id)

    def _sync_panel_area(self, panel_id: str) -> None:
        """Sync the sidebar icon position with the panel's current dock area.

        If a panel has been dragged to a different area, move its sidebar
        icon to the corresponding section:
        - Left or right dock areas → top section (side panels)
        - Bottom dock area → bottom section

        Args:
            panel_id: Panel identifier.
        """
        widget = self._panel_widgets.get(panel_id)
        if widget is None:
            return

        # Get the current dock area
        dock_area_widget = widget.dockAreaWidget()
        if dock_area_widget is None:
            return

        # Detect the current zone using zone set lookup
        current_area = self._detect_panel_area(widget)
        if current_area is None:
            return

        stored_area = self._panel_areas.get(panel_id)
        if stored_area == current_area:
            return  # No change

        # Area has changed - update tracking and register the new dock area
        old_area = stored_area
        self._panel_areas[panel_id] = current_area
        self._register_dock_area(dock_area_widget, current_area)
        logger.info("Panel {} moved from {} to {}", panel_id, old_area, current_area)

        # Enforce exclusive visibility in the new area
        # If the moved panel is visible, hide other visible panels in the same area
        if current_area in ("left", "bottom") and not widget.isClosed():
            for other_id, other_area in self._panel_areas.items():
                if other_id != panel_id and other_area == current_area:
                    other_widget = self._panel_widgets.get(other_id)
                    if other_widget and not other_widget.isClosed():
                        other_widget.toggleView(False)
                        # Sidebar icon will be deactivated via viewToggled signal

        # Move sidebar icon to corresponding section
        # Left and right areas → top section, bottom area → bottom section
        if self._icon_sidebar and current_area in ("left", "right", "bottom"):
            section = "top" if current_area in ("left", "right") else "bottom"
            self._icon_sidebar.move_panel_to_section(panel_id, section)

    def _detect_panel_area(self, widget: PanelDockWidget) -> str | None:
        """Detect which dock area a panel is currently in.

        Uses zone set lookup for reliable detection. When a panel is dragged
        to a new location, its dock area may change. We track known dock areas
        by zone and fall back to cohabitation detection for new areas.

        Args:
            widget: The panel dock widget.

        Returns:
            Area name ("left", "bottom", "right", "center") or None.
        """
        dock_area = widget.dockAreaWidget()
        if dock_area is None:
            return None

        # Check against known zone sets
        if dock_area is self._center_dock_area:
            return "center"
        if dock_area in self._left_dock_areas:
            return "left"
        if dock_area in self._bottom_dock_areas:
            return "bottom"
        if dock_area in self._right_dock_areas:
            return "right"

        # Panel was dragged into an existing area - find which zone
        # by checking other panels that share the same dock area
        return self._detect_zone_by_cohabitation(dock_area)

    def _detect_zone_by_cohabitation(self, dock_area) -> str | None:
        """Find zone by checking what other panels share this dock area.

        When a panel is dragged to share an existing dock area with another
        panel, we can determine the zone by looking at the other panel's
        known area assignment.

        Args:
            dock_area: The CDockAreaWidget to identify.

        Returns:
            Zone name or None if no cohabitating panel found.
        """
        for panel_id, widget in self._panel_widgets.items():
            if widget.dockAreaWidget() is dock_area:
                stored_area = self._panel_areas.get(panel_id)
                if stored_area and stored_area != "center":
                    # Register this dock area in the appropriate zone
                    self._register_dock_area(dock_area, stored_area)
                    return stored_area

        # No cohabitating panel found - fall back to position-based detection
        # This handles the case where a panel is dragged to create a new dock area
        return self._detect_zone_by_position(dock_area)

    def _detect_zone_by_position(self, dock_area) -> str | None:
        """Detect zone using position-based heuristics as a fallback.

        Used when zone sets and cohabitation don't provide an answer,
        such as when a panel is dragged to create a completely new dock area.

        Args:
            dock_area: The CDockAreaWidget to identify.

        Returns:
            Zone name or None.
        """
        if self._dock_manager is None:
            return None

        dock_area_rect = dock_area.geometry()
        dock_manager_rect = self._dock_manager.geometry()

        area_center_y = dock_area_rect.center().y()
        area_center_x = dock_area_rect.center().x()
        manager_height = dock_manager_rect.height()
        manager_width = dock_manager_rect.width()

        # Bottom zone: in lower portion of dock manager
        if area_center_y > manager_height * 0.65:
            return "bottom"

        # Left zone: in left portion
        if area_center_x < manager_width * 0.35:
            return "left"

        # Right zone: in right portion
        if area_center_x > manager_width * 0.65:
            return "right"

        # Center (we don't typically drag panels to center, but handle it)
        return "center"

    def _register_dock_area(self, dock_area, zone: str) -> None:
        """Register a dock area in the appropriate zone set.

        Args:
            dock_area: The CDockAreaWidget to register.
            zone: The zone name ("left", "bottom", "right").
        """
        if zone == "left":
            self._left_dock_areas.add(dock_area)
        elif zone == "bottom":
            self._bottom_dock_areas.add(dock_area)
        elif zone == "right":
            self._right_dock_areas.add(dock_area)

    def _on_focus_changed(
        self,
        old_widget: CDockWidget | None,
        new_widget: CDockWidget | None,
    ) -> None:
        """Handle dock widget focus changes."""
        if isinstance(new_widget, PanelDockWidget):
            self._active_panel_id = new_widget.panel_id
            self.panel_focused.emit(new_widget.panel_id)
        elif new_widget is None:
            self._active_panel_id = None

    # State persistence

    def save_state(self, settings: QSettings | None = None) -> QByteArray:
        """Save the current docking layout state.

        Args:
            settings: Optional QSettings to persist to.

        Returns:
            The state as QByteArray.
        """
        if self._state_manager is None:
            return QByteArray()

        return self._state_manager.save(settings)

    def restore_state(self, settings: QSettings | None = None) -> bool:
        """Restore docking layout state.

        Args:
            settings: Optional QSettings to restore from.

        Returns:
            True if state was restored.
        """
        if self._state_manager is None:
            return False

        return self._state_manager.restore(settings)

    def clear_state(self, settings: QSettings | None = None) -> None:
        """Clear saved docking state.

        Args:
            settings: Optional QSettings to clear from.
        """
        if self._state_manager:
            self._state_manager.clear(settings)

    # Introspection for MCP tools

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for Claude MCP tools.

        Returns:
            Dictionary with docking system state.
        """
        panels_data = []
        for panel_id, widget in self._panel_widgets.items():
            panel_info = {
                "id": panel_id,
                "title": widget.windowTitle(),
                "visible": not widget.isClosed(),
                "floating": widget.isFloating(),
                "focused": panel_id == self._active_panel_id,
                "area": self._panel_areas.get(panel_id, "unknown"),
            }
            panels_data.append(panel_info)

        # Group panels by area
        areas = {"left": [], "bottom": [], "center": []}
        for panel_id, area in self._panel_areas.items():
            if area in areas:
                areas[area].append(panel_id)

        return {
            "panels": panels_data,
            "active_panel": self._active_panel_id,
            "areas": areas,
            "architecture": "icon_strip_sidebar",
        }
