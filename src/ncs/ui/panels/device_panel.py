"""Device management panel for NCS.

Provides a panel for viewing and managing devices in the
device catalog, showing the actual ophyd device hierarchy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSplitter,
    QTabWidget,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ncs.devices import DeviceCatalog, DeviceInfo, DeviceStatus
from ncs.ui.models.device_tree import (
    DeviceFilterProxyModel,
    DeviceTreeItem,
    DeviceTreeModel,
    NodeType,
)
from ncs.ui.panels.base import BasePanel, PanelMetadata
from ncs.utils.logging import logger

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
        id="ncs.panels.devices",
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
    item_selected = Signal(object)  # DeviceTreeItem

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

        # Kind filter checkboxes (hinted and normal shown by default)
        filter_layout.addWidget(QLabel("Kind:"))

        self._kind_checkboxes: dict[str, QCheckBox] = {}
        default_visible = {"hinted", "normal"}
        for kind in ["hinted", "normal", "config", "omitted"]:
            cb = QCheckBox(kind.title())
            cb.setChecked(kind in default_visible)
            cb.setToolTip(f"Show {kind} signals/devices")
            cb.stateChanged.connect(self._on_kind_filter_changed)
            self._kind_checkboxes[kind] = cb
            filter_layout.addWidget(cb)

        # Apply default filter
        self._proxy_model.set_visible_kinds(default_visible)

        left_layout.addLayout(filter_layout)

        # Tree view
        self._tree_view = QTreeView()
        self._tree_view.setModel(self._proxy_model)
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
        right_widget = QTabWidget()

        # Overview tab
        self._overview_widget = DeviceOverviewWidget()
        right_widget.addTab(self._overview_widget, "Overview")

        # Configuration tab (placeholder)
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.addWidget(QLabel("Configuration management coming soon..."))
        config_layout.addStretch()
        right_widget.addTab(config_widget, "Configuration")

        splitter.addWidget(right_widget)

        # Set initial splitter sizes
        splitter.setSizes([350, 350])

        # Connect signals after UI is set up
        self._search_input.textChanged.connect(self._on_search_changed)
        self._tree_view.selectionModel().currentChanged.connect(self._on_selection_changed)

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
        """Handle kind filter checkbox change."""
        # Collect checked kinds
        visible_kinds = {
            kind for kind, cb in self._kind_checkboxes.items() if cb.isChecked()
        }

        # If all are checked, use None (no filtering)
        if len(visible_kinds) == len(self._kind_checkboxes):
            self._proxy_model.set_visible_kinds(None)
        else:
            self._proxy_model.set_visible_kinds(visible_kinds)

        # Expand tree to show filtered results
        if visible_kinds and len(visible_kinds) < len(self._kind_checkboxes):
            self._tree_view.expandAll()
        else:
            self._tree_view.collapseAll()

    @Slot()
    def _on_selection_changed(self) -> None:
        """Handle tree selection change."""
        index = self._tree_view.currentIndex()
        if not index.isValid():
            self._overview_widget.set_item(None)
            return

        # Map proxy index to source
        source_index = self._proxy_model.mapToSource(index)

        # Get the tree item
        item = source_index.internalPointer()
        if isinstance(item, DeviceTreeItem):
            self._overview_widget.set_item(item)
            self.item_selected.emit(item)
        else:
            self._overview_widget.set_item(None)

    @Slot(object)
    def _on_device_changed(self, _: Any) -> None:
        """Handle device added/removed from catalog."""
        self._refresh()

    # === Introspection ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get device panel-specific introspection data."""
        selected_item = None
        index = self._tree_view.currentIndex()
        if index.isValid():
            source_index = self._proxy_model.mapToSource(index)
            item = source_index.internalPointer()
            if isinstance(item, DeviceTreeItem):
                selected_item = {
                    "name": item.name,
                    "type": item.node_type.value,
                    "value": item._get_value(),
                    "has_device_info": item.device_info is not None,
                }

        # Get visible kinds
        visible_kinds = self._proxy_model.get_visible_kinds()
        kind_filter = list(visible_kinds) if visible_kinds else None

        return {
            "selected_item": selected_item,
            "search_text": self._search_input.text(),
            "kind_filter": kind_filter,
            "device_count": self._model.rowCount(),
            "catalog_connected": self._catalog.is_connected,
        }

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
        valid_kinds = {"hinted", "normal", "config", "omitted"}

        if kinds is None:
            # Show all - check all boxes
            for cb in self._kind_checkboxes.values():
                cb.setChecked(True)
        else:
            # Filter to specified kinds
            for kind, cb in self._kind_checkboxes.items():
                cb.setChecked(kind in kinds)

        return True
