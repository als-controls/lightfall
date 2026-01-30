"""Device management panel for NCS.

Provides a panel for viewing and managing devices in the
device catalog, showing the actual ophyd device hierarchy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSplitter,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from lucid.devices import DeviceCatalog
from lucid.ui.models.device_tree import (
    DeviceFilterProxyModel,
    DeviceTreeItem,
    DeviceTreeModel,
)
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.widgets import DeviceControlWidget
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class DeviceOverviewWidget(QWidget):
    """Widget showing device/signal details and current status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the overview widget."""
        super().__init__(parent)
        self._current_item: DeviceTreeItem | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Device/Signal info group
        info_group = QGroupBox("Information")
        info_layout = QFormLayout(info_group)

        self._name_label = QLabel("-")
        self._name_label.setStyleSheet("font-weight: bold;")
        info_layout.addRow("Name:", self._name_label)

        self._type_label = QLabel("-")
        info_layout.addRow("Type:", self._type_label)

        self._class_label = QLabel("-")
        self._class_label.setWordWrap(True)
        info_layout.addRow("Class:", self._class_label)

        self._description_label = QLabel("-")
        self._description_label.setWordWrap(True)
        info_layout.addRow("Description:", self._description_label)

        layout.addWidget(info_group)

        # Status group
        status_group = QGroupBox("Current State")
        status_layout = QFormLayout(status_group)

        self._value_label = QLabel("-")
        status_layout.addRow("Value:", self._value_label)

        self._status_label = QLabel("-")
        status_layout.addRow("Status:", self._status_label)

        self._children_label = QLabel("-")
        status_layout.addRow("Components:", self._children_label)

        layout.addWidget(status_group)

        # Metadata group (for top-level devices)
        self._meta_group = QGroupBox("Device Metadata")
        meta_layout = QFormLayout(self._meta_group)

        self._category_label = QLabel("-")
        meta_layout.addRow("Category:", self._category_label)

        self._prefix_label = QLabel("-")
        meta_layout.addRow("Prefix:", self._prefix_label)

        self._location_label = QLabel("-")
        meta_layout.addRow("Location:", self._location_label)

        self._tags_label = QLabel("-")
        self._tags_label.setWordWrap(True)
        meta_layout.addRow("Tags:", self._tags_label)

        layout.addWidget(self._meta_group)

        layout.addStretch()

    def set_item(self, item: DeviceTreeItem | None) -> None:
        """Set the item to display.

        Args:
            item: Tree item to display or None to clear.
        """
        self._current_item = item

        if item is None:
            self._clear()
            return

        # Basic info
        self._name_label.setText(item.name)
        self._type_label.setText(item.node_type.value.title())

        # Class info
        if item.ophyd_obj is not None:
            cls_name = type(item.ophyd_obj).__name__
            module = type(item.ophyd_obj).__module__
            self._class_label.setText(f"{module}.{cls_name}")
        else:
            self._class_label.setText("-")

        # Description from device_info
        if item.device_info:
            self._description_label.setText(item.device_info.description or "-")
        else:
            self._description_label.setText("-")

        # Current value
        value = item._get_value()
        self._value_label.setText(value if value else "-")

        # Status
        status = item._get_status()
        self._status_label.setText(status if status else "-")
        if status in ("online", "connected"):
            self._status_label.setStyleSheet("color: green;")
        elif status == "error":
            self._status_label.setStyleSheet("color: red;")
        else:
            self._status_label.setStyleSheet("")

        # Components count
        self._children_label.setText(str(item.child_count()))

        # Show/hide metadata group based on whether we have device_info
        if item.device_info:
            self._meta_group.setVisible(True)
            self._category_label.setText(item.device_info.category.value.title())
            self._prefix_label.setText(item.device_info.prefix or "-")
            self._location_label.setText(item.device_info.location or "-")
            if item.device_info.tags:
                self._tags_label.setText(", ".join(item.device_info.tags))
            else:
                self._tags_label.setText("-")
        else:
            self._meta_group.setVisible(False)

    def _clear(self) -> None:
        """Clear all fields."""
        self._name_label.setText("-")
        self._type_label.setText("-")
        self._class_label.setText("-")
        self._description_label.setText("-")
        self._value_label.setText("-")
        self._status_label.setText("-")
        self._status_label.setStyleSheet("")
        self._children_label.setText("-")
        self._meta_group.setVisible(False)


class DevicePanel(BasePanel):
    """Panel for device management showing ophyd device hierarchy.

    DevicePanel provides:
    - Tree view of actual ophyd device structure (not categories)
    - Device/signal details and status
    - Search and filtering
    - Device value monitoring

    This panel uses Qt Model/View architecture with DeviceTreeModel.
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.devices",
        name="Devices",
        description="View and manage control system devices",
        icon="devices",
        category="Core",
        required_permission=None,
        singleton=True,
        closable=True,
        keywords=["device", "motor", "detector", "hardware", "equipment", "signal"],
    )

    # Signals
    item_selected = Signal(object)  # DeviceTreeItem (single, for backwards compat)
    items_selected = Signal(list)  # list[DeviceTreeItem] (multi-selection)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the device panel."""
        self._catalog = DeviceCatalog.get_instance()

        # Create model before calling super().__init__ which calls _setup_ui
        self._model = DeviceTreeModel(self._catalog)
        self._proxy_model = DeviceFilterProxyModel()
        self._proxy_model.setSourceModel(self._model)

        super().__init__(parent)

        # Connect catalog signals
        self._catalog.device_added.connect(self._on_device_changed)
        self._catalog.device_removed.connect(self._on_device_changed)

    def _setup_ui(self) -> None:
        """Setup the panel UI."""
        # Main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._layout.addWidget(splitter)

        # Left side: device tree
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = self._create_toolbar()
        left_layout.addWidget(toolbar)

        # Search and filter row
        filter_layout = QHBoxLayout()

        # Search box
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search devices and signals...")
        self._search_input.setClearButtonEnabled(True)
        filter_layout.addWidget(self._search_input, stretch=1)

        # Kind filter dropdown menu (hinted and normal shown by default)
        self._kind_actions: dict[str, QAction] = {}
        default_visible = {"hinted", "normal"}

        kind_menu = QMenu(self)
        for kind in ["hinted", "normal", "config", "omitted"]:
            action = QAction(kind.title(), self)
            action.setCheckable(True)
            action.setChecked(kind in default_visible)
            action.setData(kind)
            action.triggered.connect(self._on_kind_filter_changed)
            self._kind_actions[kind] = action
            kind_menu.addAction(action)

        self._kind_button = QToolButton()
        self._kind_button.setText("Kind")
        self._kind_button.setToolTip("Filter by signal/device kind")
        self._kind_button.setMenu(kind_menu)
        self._kind_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        filter_layout.addWidget(self._kind_button)

        # Apply default filter
        self._proxy_model.set_visible_kinds(default_visible)

        left_layout.addLayout(filter_layout)

        # Tree view with multi-selection support
        self._tree_view = QTreeView()
        self._tree_view.setModel(self._proxy_model)
        self._tree_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setAnimated(True)
        self._tree_view.setExpandsOnDoubleClick(True)
        self._tree_view.setSortingEnabled(True)
        self._tree_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        # Configure header (5 columns: Name, Value, Type, Kind, Status)
        header = self._tree_view.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        # Set reasonable default column widths
        self._tree_view.setColumnWidth(0, 200)

        left_layout.addWidget(self._tree_view)
        splitter.addWidget(left_widget)

        # Right side: tabs with details
        self._right_tabs = QTabWidget()

        # Control tab (dynamic device control UI) - first tab
        self._control_widget = DeviceControlWidget()
        self._control_widget.control_error.connect(self._on_control_error)
        self._right_tabs.addTab(self._control_widget, "Control")

        # Info tab (device details)
        self._overview_widget = DeviceOverviewWidget()
        self._right_tabs.addTab(self._overview_widget, "Info")

        splitter.addWidget(self._right_tabs)

        # Set initial splitter sizes
        splitter.setSizes([350, 350])

        # Connect signals after UI is set up
        self._search_input.textChanged.connect(self._on_search_changed)
        self._tree_view.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # Tree starts collapsed
        self._tree_view.collapseAll()

    def _create_toolbar(self) -> QToolBar:
        """Create the panel toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        # Refresh action
        refresh_action = QAction("Refresh", self)
        refresh_action.setToolTip("Refresh device tree")
        refresh_action.triggered.connect(self._refresh)
        toolbar.addAction(refresh_action)

        toolbar.addSeparator()

        # Expand all
        expand_action = QAction("Expand All", self)
        expand_action.triggered.connect(lambda: self._tree_view.expandAll())
        toolbar.addAction(expand_action)

        # Collapse all
        collapse_action = QAction("Collapse", self)
        collapse_action.triggered.connect(lambda: self._tree_view.collapseAll())
        toolbar.addAction(collapse_action)

        toolbar.addSeparator()

        # Expand to depth
        depth1_action = QAction("Depth 1", self)
        depth1_action.setToolTip("Expand to depth 1 (devices only)")
        depth1_action.triggered.connect(lambda: self._expand_to_depth(0))
        toolbar.addAction(depth1_action)

        depth2_action = QAction("Depth 2", self)
        depth2_action.setToolTip("Expand to depth 2 (devices + components)")
        depth2_action.triggered.connect(lambda: self._expand_to_depth(1))
        toolbar.addAction(depth2_action)

        return toolbar

    def _expand_to_depth(self, depth: int) -> None:
        """Expand tree to specified depth."""
        self._tree_view.collapseAll()
        self._tree_view.expandToDepth(depth)

    def _refresh(self) -> None:
        """Refresh the device model."""
        self._model.refresh()
        logger.debug("Device tree refreshed")

    # === Signal Handlers ===

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        """Handle search text change."""
        self._proxy_model.setFilterRegularExpression(text)

        # Expand all when searching to show results, collapse when cleared
        if text:
            self._tree_view.expandAll()
        else:
            self._tree_view.collapseAll()

    @Slot()
    def _on_kind_filter_changed(self) -> None:
        """Handle kind filter menu action change."""
        # Collect checked kinds from menu actions
        visible_kinds = {
            kind for kind, action in self._kind_actions.items() if action.isChecked()
        }

        # If all are checked, use None (no filtering)
        if len(visible_kinds) == len(self._kind_actions):
            self._proxy_model.set_visible_kinds(None)
        else:
            self._proxy_model.set_visible_kinds(visible_kinds)

        # Expand tree to show filtered results
        if visible_kinds and len(visible_kinds) < len(self._kind_actions):
            self._tree_view.expandAll()
        else:
            self._tree_view.collapseAll()

    @Slot()
    def _on_selection_changed(self) -> None:
        """Handle tree selection change (supports multi-selection)."""
        # Get all selected indices
        selection = self._tree_view.selectionModel().selectedIndexes()

        # Filter to only column 0 (name column) to avoid duplicates
        selected_items: list[DeviceTreeItem] = []
        seen_items: set[int] = set()

        for proxy_index in selection:
            if proxy_index.column() != 0:
                continue

            source_index = self._proxy_model.mapToSource(proxy_index)
            item = source_index.internalPointer()

            if isinstance(item, DeviceTreeItem):
                item_id = id(item)
                if item_id not in seen_items:
                    seen_items.add(item_id)
                    selected_items.append(item)

        # Update overview with first selected item (or clear)
        if selected_items:
            self._overview_widget.set_item(selected_items[0])
            # Emit both signals for compatibility
            self.item_selected.emit(selected_items[0])
            self.items_selected.emit(selected_items)
        else:
            self._overview_widget.set_item(None)
            self.items_selected.emit([])

        # Update control widget with all selected items
        self._control_widget.set_items(selected_items)

    @Slot(str)
    def _on_control_error(self, message: str) -> None:
        """Handle control error from device control widget."""
        logger.warning("Device control error: {}", message)

    @Slot(object)
    def _on_device_changed(self, _: Any) -> None:
        """Handle device added/removed from catalog."""
        self._refresh()

    # === Introspection ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get device panel-specific introspection data."""
        # Get all selected items
        selected_items = self._get_selected_items()
        selected_items_data = [
            {
                "name": item.name,
                "type": item.node_type.value,
                "value": item._get_value(),
                "has_device_info": item.device_info is not None,
            }
            for item in selected_items
        ]

        # Get visible kinds
        visible_kinds = self._proxy_model.get_visible_kinds()
        kind_filter = list(visible_kinds) if visible_kinds else None

        return {
            "selected_items": selected_items_data,
            "selected_count": len(selected_items),
            "search_text": self._search_input.text(),
            "kind_filter": kind_filter,
            "device_count": self._model.rowCount(),
            "catalog_connected": self._catalog.is_connected,
            "control_widget": self._control_widget.get_introspection_data(),
        }

    def _get_selected_items(self) -> list[DeviceTreeItem]:
        """Get all currently selected DeviceTreeItems."""
        selection = self._tree_view.selectionModel().selectedIndexes()
        items: list[DeviceTreeItem] = []
        seen: set[int] = set()

        for proxy_index in selection:
            if proxy_index.column() != 0:
                continue
            source_index = self._proxy_model.mapToSource(proxy_index)
            item = source_index.internalPointer()
            if isinstance(item, DeviceTreeItem):
                item_id = id(item)
                if item_id not in seen:
                    seen.add(item_id)
                    items.append(item)
        return items

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel."""
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
        """Action: Refresh the device tree."""
        self._refresh()
        return True

    def action_search(self, query: str) -> bool:
        """Action: Search for devices/signals.

        Args:
            query: Search query string.

        Returns:
            True if search was performed.
        """
        self._search_input.setText(query)
        return True

    def action_expand_all(self) -> bool:
        """Action: Expand all tree nodes."""
        self._tree_view.expandAll()
        return True

    def action_collapse_all(self) -> bool:
        """Action: Collapse all tree nodes."""
        self._tree_view.collapseAll()
        return True

    def action_filter_by_kind(self, kinds: list[str] | None) -> bool:
        """Action: Filter by signal/device kind.

        Args:
            kinds: List of kind names to show (hinted, normal, config, omitted),
                   or None to show all.

        Returns:
            True if filter was applied.
        """
        if kinds is None:
            # Show all - check all actions
            for action in self._kind_actions.values():
                action.setChecked(True)
        else:
            # Filter to specified kinds
            for kind, action in self._kind_actions.items():
                action.setChecked(kind in kinds)

        # Trigger the filter update
        self._on_kind_filter_changed()
        return True
