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

        # Create dock widget
        widget = PanelDockWidget(panel)
        self._panel_widgets[panel_id] = widget

        # Determine area from metadata if not specified
        if area is None:
            area = panel.panel_metadata.default_area

        # Store the area for this panel
        self._panel_areas[panel_id] = area

        # Get dock widget area
        dock_area = AREA_MAP.get(area, CenterDockWidgetArea)

        # Configure widget based on area
        if area in ("left", "bottom"):
            # Side panels: show title bar but style it as a simple header (not tab-like)
            # We keep the tab visible but it will be styled via CSS to look like a title bar

            # Set size hints for proper initial sizing
            if area == "left":
                widget.setMinimumWidth(LEFT_PANEL_WIDTH)
                panel.setMinimumWidth(LEFT_PANEL_WIDTH)
            else:  # bottom
                widget.setMinimumHeight(BOTTOM_PANEL_HEIGHT)
                panel.setMinimumHeight(BOTTOM_PANEL_HEIGHT)

            # Add to dock manager
            self._dock_manager.addDockWidget(dock_area, widget)
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
            # Center panel: keep tab, always visible
            self._dock_manager.addDockWidget(dock_area, widget)

        # Connect visibility changes to sync sidebar
        widget.viewToggled.connect(
            lambda visible, pid=panel_id: self._on_panel_visibility_changed(pid, visible)
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

        if self._dock_manager:
            self._dock_manager.setDockWidgetFocused(widget)

        return True

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

    def _on_panel_visibility_changed(self, panel_id: str, visible: bool) -> None:
        """Handle panel visibility change (e.g., closed via X button).

        Keeps the sidebar in sync with actual panel state.

        Args:
            panel_id: The panel whose visibility changed.
            visible: Whether the panel is now visible.
        """
        if self._icon_sidebar:
            self._icon_sidebar.set_panel_active(panel_id, visible)

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
