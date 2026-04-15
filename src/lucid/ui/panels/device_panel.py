"""Device management panel for NCS.

Provides a tabbed panel for viewing and managing devices:
- Favorites tab: compact motor control widgets for favorited devices
- All tab: full device tree with search and filtering
- Device tabs: individual device controller widgets opened on demand
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QCoreApplication, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QTabBar,
    QTabWidget,
    QWidget,
)

from lucid.devices import DeviceCatalog
from lucid.ui.events import DeviceFocusEvent, DeviceSelectEvent
from lucid.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.panels.registry import PanelRegistry
from lucid.ui.preferences.manager import PreferencesManager
from lucid.ui.widgets.device_control import DeviceControlWidget
from lucid.ui.widgets.device_tree_tab import DeviceTreeTab
from lucid.ui.widgets.favorites_tab import FavoritesTab
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class DevicePanel(BasePanel):
    """Tabbed panel for device management.

    Tab layout:
    - Tab 0: Favorites (unclosable) — compact motor widgets
    - Tab 1: All (unclosable) — device tree with search/filter
    - Tab 2+: Device controllers (closable) — opened on demand
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.devices",
        name="Devices",
        description="View and manage control system devices",
        icon="mdi.microwave",
        category="Core",
        required_permission=None,
        singleton=True,
        closable=True,
        keywords=["device", "motor", "detector", "hardware", "equipment", "signal"],
        default_area="left",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=1,
    )

    # Signals (preserved for backward compat)
    item_selected = Signal(object)  # DeviceTreeItem
    items_selected = Signal(list)  # list[DeviceTreeItem]

    def __init__(self, parent: QWidget | None = None) -> None:
        logger.info("DevicePanel.__init__() START")
        self._catalog = DeviceCatalog.get_instance()
        self._prefs = PreferencesManager.get_instance()

        # Track open device controller tabs: device_id -> widget
        self._device_tabs: dict[str, QWidget] = {}

        super().__init__(parent)

        # Load saved favorites
        self._load_favorites()

        # Connect catalog signals for favorites updates
        self._catalog.device_connected.connect(self._favorites_tab.on_device_connected)

        logger.info("DevicePanel.__init__() END")

    def _setup_ui(self) -> None:
        """Setup the tabbed panel UI."""
        # Main tab widget
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self._layout.addWidget(self._tabs)

        # Tab 0: Favorites
        self._favorites_tab = FavoritesTab(catalog=self._catalog)
        self._favorites_tab.open_controller_requested.connect(
            self._open_device_tab_by_id
        )
        self._favorites_tab.favorites_changed.connect(self._save_favorites)
        self._tabs.addTab(self._favorites_tab, "Favorites")

        # Tab 1: All (device tree)
        self._tree_tab = DeviceTreeTab(catalog=self._catalog)
        self._tree_tab.set_is_favorite_fn(self._favorites_tab.is_favorite)
        self._tree_tab.device_open_requested.connect(self._open_device_tab)
        self._tree_tab.favorite_toggled.connect(self._on_favorite_toggled)
        self._tree_tab.item_selected.connect(self._on_item_selected)
        self._tree_tab.items_selected.connect(self.items_selected)
        self._tabs.addTab(self._tree_tab, "All")

        # Hide close buttons on first two tabs
        tab_bar = self._tabs.tabBar()
        tab_bar.setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
        tab_bar.setTabButton(1, QTabBar.ButtonPosition.RightSide, None)

    # === Selection & Cross-Panel Events ===

    @Slot(object)
    def _on_item_selected(self, item: DeviceTreeItem) -> None:
        """Handle item selection — forward signal and post focus event."""
        self.item_selected.emit(item)
        self._post_device_focus_event(item)

    def _post_device_focus_event(self, item: DeviceTreeItem) -> None:
        """Post a DeviceFocusEvent to the Synoptic panel."""
        if item.device_info is None:
            return
        device_id = str(item.device_info.id)
        device_name = item.device_info.name
        registry = PanelRegistry.get_instance()
        synoptic_panel = registry.get_singleton("lucid.panels.synoptic")
        if synoptic_panel is not None:
            event = DeviceFocusEvent(device_id, device_name)
            QCoreApplication.postEvent(synoptic_panel, event)
            logger.debug("Posted DeviceFocusEvent for device: {}", device_name)

    # === Favorites ===

    def _load_favorites(self) -> None:
        """Load favorites from preferences."""
        saved = self._prefs.get("device_favorites", [])
        if saved:
            self._favorites_tab.set_favorites(saved)

    @Slot(list)
    def _save_favorites(self, favorite_ids: list[str]) -> None:
        """Save favorites to preferences."""
        self._prefs.set("device_favorites", favorite_ids)

    @Slot(str, bool)
    def _on_favorite_toggled(self, device_id: str, is_favorite: bool) -> None:
        """Handle favorite toggle from tree context menu."""
        if is_favorite:
            self._favorites_tab.add_favorite(device_id)
        else:
            self._favorites_tab.remove_favorite(device_id)

    # === Device Controller Tabs ===

    @Slot(object)
    def _open_device_tab(self, item: DeviceTreeItem) -> None:
        """Open a device controller in a new tab (or focus existing)."""
        if item.device_info is None:
            return

        device_id = str(item.device_info.id)

        # If already open, focus it
        if device_id in self._device_tabs:
            widget = self._device_tabs[device_id]
            idx = self._tabs.indexOf(widget)
            if idx >= 0:
                self._tabs.setCurrentIndex(idx)
            return

        # Create a new DeviceControlWidget for this device
        control = DeviceControlWidget()
        control.set_items([item])
        control.control_error.connect(self._on_control_error)

        # Add the tab
        self._tabs.addTab(control, item.name)
        self._device_tabs[device_id] = control

        # Focus the new tab
        self._tabs.setCurrentWidget(control)

    def _open_device_tab_by_id(self, device_id: str) -> None:
        """Open a device controller tab by device ID."""
        # If already open, focus
        if device_id in self._device_tabs:
            widget = self._device_tabs[device_id]
            idx = self._tabs.indexOf(widget)
            if idx >= 0:
                self._tabs.setCurrentIndex(idx)
            return

        # Find the device in the tree model and open
        root = self._tree_tab.model.root_item
        item = self._tree_tab._find_device_item(root, device_id)
        if item is not None:
            self._open_device_tab(item)

    @Slot(int)
    def _on_tab_close_requested(self, index: int) -> None:
        """Handle tab close — ignore for tabs 0 and 1."""
        if index < 2:
            return

        widget = self._tabs.widget(index)

        # Find and remove from tracking dict
        device_id_to_remove = None
        for device_id, w in self._device_tabs.items():
            if w is widget:
                device_id_to_remove = device_id
                break

        if device_id_to_remove is not None:
            del self._device_tabs[device_id_to_remove]

        # Remove and destroy
        self._tabs.removeTab(index)
        if widget is not None:
            widget.close()
            widget.deleteLater()

    @Slot(str)
    def _on_control_error(self, message: str) -> None:
        """Handle control error from a device controller tab."""
        logger.warning("Device control error: {}", message)

    # === Event Handling (preserved) ===

    def event(self, event) -> bool:
        if event.type() == DeviceSelectEvent.EventType:
            self._handle_device_select_event(event)
            return True
        if event.type() == DeviceFocusEvent.EventType:
            self._handle_device_focus_event(event)
            return True
        return super().event(event)

    def _handle_device_select_event(self, event: DeviceSelectEvent) -> None:
        self._tree_tab.select_device_by_id(event.device_id)

    def _handle_device_focus_event(self, event: DeviceFocusEvent) -> None:
        self._tree_tab.select_device_by_id(event.device_id)

    # === Introspection (preserved) ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        return {
            "active_tab": self._tabs.tabText(self._tabs.currentIndex()),
            "tab_count": self._tabs.count(),
            "open_device_tabs": list(self._device_tabs.keys()),
            "favorites_count": len(self._favorites_tab.get_favorite_ids()),
            "search_text": self._tree_tab.get_search_text(),
            "kind_filter": (
                list(self._tree_tab.get_visible_kinds())
                if self._tree_tab.get_visible_kinds()
                else None
            ),
            "device_count": self._tree_tab.model.rowCount(),
            "catalog_connected": self._catalog.is_connected,
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "refresh",
                "description": "Refresh the device tree",
                "method": "action_refresh",
            },
            {
                "name": "search",
                "description": "Search for devices/signals",
                "method": "action_search",
                "parameters": {"query": "string"},
            },
            {
                "name": "expand_all",
                "description": "Expand entire tree",
                "method": "action_expand_all",
            },
            {
                "name": "collapse_all",
                "description": "Collapse entire tree",
                "method": "action_collapse_all",
            },
            {
                "name": "filter_by_kind",
                "description": "Filter by signal/device kind",
                "method": "action_filter_by_kind",
                "parameters": {"kinds": "list of kind names (hinted, normal, config, omitted)"},
            },
        ])
        return actions

    def action_refresh(self) -> bool:
        self._tree_tab.model.refresh()
        return True

    def action_search(self, query: str) -> bool:
        self._tree_tab._search_input.setText(query)
        return True

    def action_expand_all(self) -> bool:
        self._tree_tab.tree_view.expandAll()
        return True

    def action_collapse_all(self) -> bool:
        self._tree_tab.tree_view.collapseAll()
        return True

    def action_filter_by_kind(self, kinds: list[str] | None) -> bool:
        """Filter the tree by signal/device kind.

        Args:
            kinds: List of kind names to show, or None to show all.
        """
        if kinds is None:
            for action in self._tree_tab._kind_actions.values():
                action.setChecked(True)
        else:
            for kind, action in self._tree_tab._kind_actions.items():
                action.setChecked(kind in kinds)
        self._tree_tab._on_kind_filter_changed()
        return True
