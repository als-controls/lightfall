"""DockingManager - Central manager for the docking system.

Uses QMainWindow's native QDockWidget support with a custom icon strip
sidebar for VS Code/PyCharm-like panel navigation.

Architecture:
    NCSMainWindow
    └── QHBoxLayout
        ├── IconStripSidebar (custom icon strip)
        └── inner QMainWindow (hosts QDockWidgets + central widget)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QWidget

from lightfall.ui.docking.icon_sidebar import IconStripSidebar
from lightfall.ui.docking.state import DockingState
from lightfall.ui.docking.widget import PanelDockWidget
from lightfall.ui.panels.base import PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry
from lightfall.utils.logging import logger

# Default sizes for side panels (in pixels)
LEFT_PANEL_WIDTH = 350
BOTTOM_PANEL_HEIGHT = 250

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


# Map area names to Qt DockWidgetArea
AREA_MAP: dict[str, Qt.DockWidgetArea] = {
    "left": Qt.DockWidgetArea.LeftDockWidgetArea,
    "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
    "right": Qt.DockWidgetArea.RightDockWidgetArea,
}


class DockingManager(QObject):
    """Manages the docking system using native QDockWidget.

    Uses an inner QMainWindow to host QDockWidgets alongside a custom
    icon strip sidebar:
    - Left dock area for primary tools (Bluesky, Devices) — one visible at a time
    - Bottom dock area for auxiliary panels (Claude, Documents) — one visible at a time
    - Central widget for always-visible panels (Logbook)

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
            main_window: The outer main window.
            parent: Optional parent object.
        """
        super().__init__(parent)
        self._outer_window = main_window
        self._inner_window: QMainWindow | None = None
        self._icon_sidebar: IconStripSidebar | None = None
        self._panel_widgets: dict[str, PanelDockWidget] = {}
        self._panel_areas: dict[str, str] = {}  # panel_id -> "left", "bottom", "center"
        self._state_manager: DockingState | None = None
        self._active_panel_id: str | None = None
        self._central_widget: QWidget | None = None

        # Deferred (lazy) panel tracking
        self._deferred_panels: dict[str, str] = {}  # panel_id -> area
        self._deferred_metadata: dict[str, PanelMetadata] = {}  # panel_id -> metadata

    def initialize(self) -> None:
        """Initialize the docking system.

        Creates:
        - Central widget with horizontal layout
        - IconStripSidebar on the left
        - Inner QMainWindow for hosting dock widgets

        Must be called after the main window is created but before
        adding any panels.
        """
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

        # Create inner QMainWindow (hosts QDockWidgets)
        self._inner_window = QMainWindow()
        self._inner_window.setObjectName("InnerDockWindow")
        self._inner_window.setDockNestingEnabled(True)
        self._inner_window.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._inner_window)

        # Set as outer main window central widget
        self._outer_window.setCentralWidget(self._central_widget)

        # Create state manager (uses inner window for state)
        self._state_manager = DockingState(self._inner_window)

        # Connect to panel registry signals for runtime panel registration
        registry = PanelRegistry.get_instance()
        registry.panel_registered.connect(self._on_panel_registered)
        registry.panel_unregistered.connect(self._on_panel_unregistered)

        logger.info("DockingManager initialized with QDockWidget + icon strip sidebar")

    @property
    def dock_manager(self) -> QMainWindow | None:
        """Get the inner QMainWindow that hosts dock widgets."""
        return self._inner_window

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
        - "center" → Central widget (always visible)

        Args:
            panel_id: Unique panel identifier.
            panel: The BasePanel instance.
            area: Dock area ("left", "bottom", "center").
                Defaults to panel's default_area metadata.
            add_sidebar_button: Whether to add sidebar button immediately.

        Returns:
            The created PanelDockWidget or None on failure.
        """
        if self._inner_window is None:
            logger.error("DockingManager not initialized")
            return None

        if panel_id in self._panel_widgets:
            widget = self._panel_widgets[panel_id]
            self._show_panel_exclusive(panel_id)
            return widget

        # Determine area from metadata if not specified
        if area is None:
            area = panel.panel_metadata.default_area

        # Store area
        self._panel_areas[panel_id] = area

        if area == "center":
            return self._add_center_panel(panel_id, panel)
        else:
            return self._add_dock_panel(panel_id, panel, area, add_sidebar_button)

    def _add_center_panel(self, panel_id: str, panel: BasePanel) -> PanelDockWidget | None:
        """Add a panel to the center area (no dock widget, just central widget).

        Args:
            panel_id: Panel identifier.
            panel: The BasePanel instance.

        Returns:
            None (center panels don't create PanelDockWidgets).
        """
        if self._inner_window is None:
            return None

        self._inner_window.setCentralWidget(panel)
        # We don't create a PanelDockWidget for center panels
        logger.debug("Set center panel: {}", panel_id)
        self.panel_added.emit(panel_id)
        return None

    def _add_dock_panel(
        self,
        panel_id: str,
        panel: BasePanel,
        area: str,
        add_sidebar_button: bool,
    ) -> PanelDockWidget | None:
        """Add a panel as a QDockWidget.

        Args:
            panel_id: Panel identifier.
            panel: The BasePanel instance.
            area: Dock area ("left" or "bottom").
            add_sidebar_button: Whether to add sidebar button.

        Returns:
            The created PanelDockWidget.
        """
        if self._inner_window is None:
            return None

        # Create dock widget with custom title bar
        widget = PanelDockWidget(panel, use_custom_title_bar=True)
        self._panel_widgets[panel_id] = widget

        # Get Qt dock area
        qt_area = AREA_MAP.get(area, Qt.DockWidgetArea.LeftDockWidgetArea)

        # Set size hints
        if area == "left":
            widget.setMinimumWidth(LEFT_PANEL_WIDTH)
            panel.setMinimumWidth(LEFT_PANEL_WIDTH)
        elif area == "bottom":
            widget.setMinimumHeight(BOTTOM_PANEL_HEIGHT)
            panel.setMinimumHeight(BOTTOM_PANEL_HEIGHT)

        # Add to inner window
        self._inner_window.addDockWidget(qt_area, widget)

        # Initially hidden
        widget.setVisible(False)

        # Add sidebar button
        if add_sidebar_button and self._icon_sidebar:
            self._icon_sidebar.add_panel_button(
                panel_id,
                panel.panel_metadata.icon,
                panel.panel_metadata.name,
            )

        # Connect icon changes
        if self._icon_sidebar:
            panel.icon_changed.connect(
                lambda icon_name, color, pid=panel_id: self._icon_sidebar.update_button_icon(pid, icon_name, color)
            )

        # Connect visibility changes to sync sidebar
        widget.visibilityChanged.connect(
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

    # Deferred (lazy) panel methods

    def register_deferred_panel(
        self,
        panel_id: str,
        metadata: PanelMetadata,
        area: str,
        *,
        add_sidebar_button: bool = True,
    ) -> None:
        """Register a panel for deferred (lazy) instantiation.

        Args:
            panel_id: Panel identifier.
            metadata: Panel metadata.
            area: Dock area ("left", "bottom").
            add_sidebar_button: Whether to add sidebar button immediately.
        """
        if panel_id in self._panel_widgets:
            logger.warning("Panel {} already instantiated", panel_id)
            return

        if panel_id in self._deferred_panels:
            logger.debug("Panel {} already registered as deferred", panel_id)
            return

        self._deferred_panels[panel_id] = area
        self._deferred_metadata[panel_id] = metadata

        if add_sidebar_button and self._icon_sidebar:
            self._icon_sidebar.add_panel_button(
                panel_id,
                metadata.icon,
                metadata.name,
            )

        logger.debug("Registered deferred panel {} for area {}", panel_id, area)

    def add_deferred_sidebar_button(self, panel_id: str) -> bool:
        """Add a sidebar button for a deferred panel.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if button was added.
        """
        if self._icon_sidebar is None:
            return False

        metadata = self._deferred_metadata.get(panel_id)
        if metadata is None:
            return self.add_sidebar_button(panel_id)

        self._icon_sidebar.add_panel_button(
            panel_id,
            metadata.icon,
            metadata.name,
        )
        return True

    def is_panel_deferred(self, panel_id: str) -> bool:
        """Check if a panel is registered but not yet instantiated."""
        return panel_id in self._deferred_panels

    def _instantiate_deferred_panel(self, panel_id: str) -> BasePanel | None:
        """Instantiate a deferred panel.

        Args:
            panel_id: Panel identifier.

        Returns:
            The panel instance or None.
        """
        if panel_id not in self._deferred_panels:
            return None

        area = self._deferred_panels.pop(panel_id)
        self._deferred_metadata.pop(panel_id, None)

        registry = PanelRegistry.get_instance()
        panel = registry.create(panel_id)

        if panel is None:
            logger.error("Failed to instantiate deferred panel: {}", panel_id)
            return None

        dock_widget = self.add_panel(
            panel_id,
            panel,
            area=area,
            add_sidebar_button=False,
        )

        if dock_widget is None and area != "center":
            logger.error("Failed to add deferred panel to dock: {}", panel_id)
            return None

        # Re-apply theme so new dock widget picks up child selectors
        try:
            from lightfall.ui.theme import ThemeManager
            ThemeManager.get_instance().apply_to_application()
        except Exception:
            pass

        logger.info("Instantiated deferred panel: {}", panel_id)
        return panel

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

        self._panel_areas.pop(panel_id, None)

        if self._inner_window:
            self._inner_window.removeDockWidget(widget)
        widget.deleteLater()

        logger.debug("Removed panel {}", panel_id)
        self.panel_removed.emit(panel_id)
        return True

    def get_panel(self, panel_id: str) -> BasePanel | None:
        """Get a panel by ID."""
        widget = self._panel_widgets.get(panel_id)
        if widget:
            return widget.panel
        return None

    def get_dock_widget(self, panel_id: str) -> PanelDockWidget | None:
        """Get a dock widget by panel ID."""
        return self._panel_widgets.get(panel_id)

    def list_panels(self, *, include_deferred: bool = False) -> list[str]:
        """Get list of panel IDs."""
        panels = list(self._panel_widgets.keys())
        if include_deferred:
            panels.extend(self._deferred_panels.keys())
        return panels

    def toggle_panel(self, panel_id: str) -> bool:
        """Toggle panel visibility.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if toggle was successful.
        """
        widget = self._panel_widgets.get(panel_id)
        if widget is None:
            return False

        if widget.isVisible():
            widget.setVisible(False)
        else:
            self._show_panel_exclusive(panel_id)

        return True

    def show_panel(self, panel_id: str) -> bool:
        """Show and focus a panel.

        For deferred panels, instantiates them first.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if successful.
        """
        if panel_id in self._deferred_panels:
            panel = self._instantiate_deferred_panel(panel_id)
            if panel is None:
                return False

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

        widget.setVisible(False)
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

        # For side panels, hide others in the same area
        if panel_area in ("left", "bottom"):
            for other_id, other_area in self._panel_areas.items():
                if other_id != panel_id and other_area == panel_area:
                    other_widget = self._panel_widgets.get(other_id)
                    if other_widget and other_widget.isVisible():
                        other_widget.setVisible(False)

        # Show the panel
        widget.setVisible(True)
        widget.raise_()

        return True

    def _on_sidebar_panel_toggled(self, panel_id: str, should_show: bool) -> None:
        """Handle sidebar button toggle."""
        if should_show:
            if panel_id in self._deferred_panels:
                panel = self._instantiate_deferred_panel(panel_id)
                if panel is None:
                    if self._icon_sidebar:
                        self._icon_sidebar.set_panel_active(panel_id, False)
                    return
            self._show_panel_exclusive(panel_id)
        else:
            self.hide_panel(panel_id)

    def _on_sidebar_section_changed(self, panel_id: str, new_section: str) -> None:
        """Move a panel to a different dock area when sidebar icon changes section.

        Args:
            panel_id: Panel identifier.
            new_section: Target section ("top" = left dock, "bottom" = bottom dock).
        """
        widget = self._panel_widgets.get(panel_id)
        if widget is None or self._inner_window is None:
            return

        target_area = "left" if new_section == "top" else "bottom"
        current_area = self._panel_areas.get(panel_id)

        if current_area == target_area:
            return

        was_visible = widget.isVisible()

        # Move to new area
        qt_area = AREA_MAP.get(target_area, Qt.DockWidgetArea.LeftDockWidgetArea)
        self._inner_window.removeDockWidget(widget)
        self._inner_window.addDockWidget(qt_area, widget)

        # Update tracking
        self._panel_areas[panel_id] = target_area

        # Restore visibility
        if was_visible:
            self._show_panel_exclusive(panel_id)

        logger.info("Moved panel {} from {} to {}", panel_id, current_area, target_area)

    def _on_panel_visibility_changed(self, panel_id: str, visible: bool) -> None:
        """Handle panel visibility change."""
        if self._icon_sidebar:
            self._icon_sidebar.set_panel_active(panel_id, visible)

    # Runtime panel registration handlers

    def _on_panel_registered(self, panel_id: str, metadata: PanelMetadata) -> None:
        """Handle runtime panel registration from PanelRegistry."""
        if panel_id in self._panel_widgets or panel_id in self._deferred_panels:
            return

        if metadata.default_area == "center":
            return

        section = "top" if metadata.default_area == "left" else "bottom"

        self._deferred_panels[panel_id] = metadata.default_area
        self._deferred_metadata[panel_id] = metadata

        if self._icon_sidebar:
            self._icon_sidebar.insert_panel_button_sorted(
                panel_id,
                metadata.icon,
                metadata.name,
                metadata.sidebar_order,
                section,
            )

        logger.debug("Auto-registered runtime panel: {}", panel_id)

    def _on_panel_unregistered(self, panel_id: str) -> None:
        """Handle panel unregistration from PanelRegistry."""
        self._deferred_panels.pop(panel_id, None)
        self._deferred_metadata.pop(panel_id, None)

        if self._icon_sidebar:
            self._icon_sidebar.remove_panel_button(panel_id)

        if panel_id in self._panel_widgets:
            self.remove_panel(panel_id)

        logger.debug("Removed unregistered panel: {}", panel_id)

    # State persistence

    def save_state(self, settings: QSettings | None = None) -> QByteArray:
        """Save the current docking layout state."""
        if self._state_manager is None:
            return QByteArray()
        return self._state_manager.save(settings)

    def restore_state(self, settings: QSettings | None = None) -> bool:
        """Restore docking layout state."""
        if self._state_manager is None:
            return False
        return self._state_manager.restore(settings)

    def clear_state(self, settings: QSettings | None = None) -> None:
        """Clear saved docking state."""
        if self._state_manager:
            self._state_manager.clear(settings)

    # Introspection

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        panels_data = []
        for panel_id, widget in self._panel_widgets.items():
            panel_info = {
                "id": panel_id,
                "title": widget.windowTitle(),
                "visible": widget.isVisible(),
                "floating": widget.isFloating(),
                "focused": panel_id == self._active_panel_id,
                "area": self._panel_areas.get(panel_id, "unknown"),
                "deferred": False,
            }
            panels_data.append(panel_info)

        for panel_id, area in self._deferred_panels.items():
            metadata = self._deferred_metadata.get(panel_id)
            panel_info = {
                "id": panel_id,
                "title": metadata.name if metadata else panel_id,
                "visible": False,
                "floating": False,
                "focused": False,
                "area": area,
                "deferred": True,
            }
            panels_data.append(panel_info)

        areas: dict[str, list[str]] = {"left": [], "bottom": [], "center": []}
        for panel_id, area in self._panel_areas.items():
            if area in areas:
                areas[area].append(panel_id)
        for panel_id, area in self._deferred_panels.items():
            if area in areas:
                areas[area].append(panel_id)

        return {
            "panels": panels_data,
            "active_panel": self._active_panel_id,
            "areas": areas,
            "architecture": "qdockwidget_icon_sidebar",
            "deferred_count": len(self._deferred_panels),
        }
