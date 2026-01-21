"""Device management panel for NCS.

Provides a panel for viewing and managing devices in the
device catalog, including status monitoring, configuration,
and history viewing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ncs.devices import (
    DeviceCatalog,
    DeviceCategory,
    DeviceInfo,
    DeviceMetricsCollector,
    DeviceStatus,
)
from ncs.ui.panels.base import BasePanel, PanelMetadata
from ncs.utils.logging import logger

if TYPE_CHECKING:
    pass


class DeviceOverviewWidget(QWidget):
    """Widget showing device details and current status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the overview widget."""
        super().__init__(parent)
        self._device: DeviceInfo | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Device info group
        info_group = QGroupBox("Device Information")
        info_layout = QFormLayout(info_group)

        self._name_label = QLabel("-")
        self._name_label.setStyleSheet("font-weight: bold;")
        info_layout.addRow("Name:", self._name_label)

        self._category_label = QLabel("-")
        info_layout.addRow("Category:", self._category_label)

        self._description_label = QLabel("-")
        self._description_label.setWordWrap(True)
        info_layout.addRow("Description:", self._description_label)

        self._prefix_label = QLabel("-")
        info_layout.addRow("Prefix:", self._prefix_label)

        self._class_label = QLabel("-")
        info_layout.addRow("Class:", self._class_label)

        self._location_label = QLabel("-")
        info_layout.addRow("Location:", self._location_label)

        layout.addWidget(info_group)

        # Status group
        status_group = QGroupBox("Current Status")
        status_layout = QFormLayout(status_group)

        self._status_label = QLabel("-")
        status_layout.addRow("Status:", self._status_label)

        self._connected_label = QLabel("-")
        status_layout.addRow("Connected:", self._connected_label)

        self._position_label = QLabel("-")
        status_layout.addRow("Position:", self._position_label)

        self._value_label = QLabel("-")
        status_layout.addRow("Value:", self._value_label)

        layout.addWidget(status_group)

        # Tags
        tags_group = QGroupBox("Tags")
        tags_layout = QVBoxLayout(tags_group)
        self._tags_label = QLabel("-")
        self._tags_label.setWordWrap(True)
        tags_layout.addWidget(self._tags_label)
        layout.addWidget(tags_group)

        layout.addStretch()

    def set_device(self, device: DeviceInfo | None) -> None:
        """Set the device to display.

        Args:
            device: Device to display or None to clear.
        """
        self._device = device

        if device is None:
            self._clear()
            return

        # Update info labels
        self._name_label.setText(device.name)
        self._category_label.setText(device.category.value.title())
        self._description_label.setText(device.description or "-")
        self._prefix_label.setText(device.prefix or "-")
        self._class_label.setText(device.device_class or "-")
        self._location_label.setText(device.location or "-")

        # Update tags
        if device.tags:
            self._tags_label.setText(", ".join(device.tags))
        else:
            self._tags_label.setText("-")

        # Update status
        self._update_status()

    def _update_status(self) -> None:
        """Update status display."""
        if self._device is None:
            return

        state = self._device.state

        if state:
            # Status with color
            status_text = state.status.value.title()
            if state.status == DeviceStatus.ONLINE:
                self._status_label.setStyleSheet("color: green;")
            elif state.status == DeviceStatus.ERROR:
                self._status_label.setStyleSheet("color: red;")
            elif state.status == DeviceStatus.OFFLINE:
                self._status_label.setStyleSheet("color: gray;")
            else:
                self._status_label.setStyleSheet("")
            self._status_label.setText(status_text)

            # Connected
            connected_text = "Yes" if state.connected else "No"
            self._connected_label.setText(connected_text)

            # Position
            if state.position is not None:
                units = self._device.metadata.get("units", "")
                self._position_label.setText(f"{state.position:.4f} {units}".strip())
            else:
                self._position_label.setText("-")

            # Value
            if state.value is not None:
                units = self._device.metadata.get("units", "")
                try:
                    self._value_label.setText(f"{float(state.value):.4f} {units}".strip())
                except (TypeError, ValueError):
                    self._value_label.setText(str(state.value))
            else:
                self._value_label.setText("-")
        else:
            self._status_label.setText("Unknown")
            self._status_label.setStyleSheet("color: gray;")
            self._connected_label.setText("-")
            self._position_label.setText("-")
            self._value_label.setText("-")

    def _clear(self) -> None:
        """Clear all fields."""
        self._name_label.setText("-")
        self._category_label.setText("-")
        self._description_label.setText("-")
        self._prefix_label.setText("-")
        self._class_label.setText("-")
        self._location_label.setText("-")
        self._status_label.setText("-")
        self._status_label.setStyleSheet("")
        self._connected_label.setText("-")
        self._position_label.setText("-")
        self._value_label.setText("-")
        self._tags_label.setText("-")


class DevicePanel(BasePanel):
    """Panel for device management.

    DevicePanel provides:
    - Device list organized by category
    - Device details and status viewing
    - Search and filtering
    - Device health overview
    - Configuration management

    This panel connects to the DeviceCatalog and provides
    a GUI for exploring and managing devices.
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
        keywords=["device", "motor", "detector", "hardware", "equipment"],
    )

    # Signals
    device_selected = Signal(object)  # DeviceInfo

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the device panel."""
        self._catalog = DeviceCatalog.get_instance()
        super().__init__(parent)

        # Connect catalog signals
        self._catalog.device_added.connect(self._on_device_added)
        self._catalog.device_removed.connect(self._on_device_removed)
        self._catalog.device_state_changed.connect(self._on_device_state_changed)

        # Initial load
        self._refresh_device_list()

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

        # Search box
        search_layout = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search devices...")
        search_layout.addWidget(self._search_input)

        self._category_filter = QComboBox()
        self._category_filter.addItem("All Categories", None)
        for cat in DeviceCategory:
            self._category_filter.addItem(cat.value.title(), cat)
        search_layout.addWidget(self._category_filter)

        left_layout.addLayout(search_layout)

        # Device tree
        self._device_tree = QTreeWidget()
        self._device_tree.setHeaderLabels(["Name", "Status", "Type"])
        self._device_tree.setRootIsDecorated(True)
        self._device_tree.setAlternatingRowColors(True)
        self._device_tree.header().setStretchLastSection(True)
        left_layout.addWidget(self._device_tree)

        # Connect signals AFTER all widgets are created
        self._search_input.textChanged.connect(self._on_search_changed)
        self._category_filter.currentIndexChanged.connect(self._on_filter_changed)
        self._device_tree.itemSelectionChanged.connect(self._on_selection_changed)

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

        # History tab (placeholder)
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)
        history_layout.addWidget(QLabel("Maintenance history coming soon..."))
        history_layout.addStretch()
        right_widget.addTab(history_widget, "History")

        splitter.addWidget(right_widget)

        # Set initial splitter sizes
        splitter.setSizes([300, 400])

    def _create_toolbar(self) -> QToolBar:
        """Create the panel toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        # Refresh action
        refresh_action = QAction("Refresh", self)
        refresh_action.setToolTip("Refresh device list")
        refresh_action.triggered.connect(self._refresh_device_list)
        toolbar.addAction(refresh_action)

        toolbar.addSeparator()

        # Expand all
        expand_action = QAction("Expand All", self)
        expand_action.triggered.connect(self._device_tree.expandAll)
        toolbar.addAction(expand_action)

        # Collapse all
        collapse_action = QAction("Collapse All", self)
        collapse_action.triggered.connect(self._device_tree.collapseAll)
        toolbar.addAction(collapse_action)

        return toolbar

    def _refresh_device_list(self) -> None:
        """Refresh the device list from the catalog."""
        self._device_tree.clear()

        # Get filter settings
        search_text = self._search_input.text().lower() if hasattr(self, '_search_input') else ""
        category_filter = self._category_filter.currentData() if hasattr(self, '_category_filter') else None

        # Get devices
        if search_text:
            devices = self._catalog.search_devices(search_text)
        else:
            devices = self._catalog.list_devices(category=category_filter)

        # Group by category
        by_category: dict[str, list[DeviceInfo]] = {}
        for device in devices:
            cat_name = device.category.value.title()
            if cat_name not in by_category:
                by_category[cat_name] = []
            by_category[cat_name].append(device)

        # Build tree
        for category_name in sorted(by_category.keys()):
            category_item = QTreeWidgetItem([category_name, "", ""])
            category_item.setExpanded(True)

            for device in sorted(by_category[category_name], key=lambda d: d.name):
                device_item = QTreeWidgetItem([
                    device.name,
                    device.state.status.value if device.state else "unknown",
                    device.device_class.split(".")[-1] if device.device_class else "",
                ])
                device_item.setData(0, Qt.ItemDataRole.UserRole, device.id)

                # Color based on status
                if device.state:
                    if device.state.status == DeviceStatus.ONLINE:
                        device_item.setForeground(1, QColor("green"))
                    elif device.state.status == DeviceStatus.ERROR:
                        device_item.setForeground(1, QColor("red"))
                    elif device.state.status == DeviceStatus.OFFLINE:
                        device_item.setForeground(1, QColor("gray"))

                category_item.addChild(device_item)

            self._device_tree.addTopLevelItem(category_item)

        # Expand all by default
        self._device_tree.expandAll()

        logger.debug("Refreshed device list with {} devices", len(devices))

    # === Signal Handlers ===

    @Slot()
    def _on_selection_changed(self) -> None:
        """Handle tree selection change."""
        items = self._device_tree.selectedItems()
        if not items:
            self._overview_widget.set_device(None)
            return

        item = items[0]
        device_id = item.data(0, Qt.ItemDataRole.UserRole)

        if device_id:
            device = self._catalog.get_device(device_id)
            if device:
                # Refresh state
                self._catalog.refresh_device_state(device_id)
                device = self._catalog.get_device(device_id)

            self._overview_widget.set_device(device)
            self.device_selected.emit(device)
        else:
            # Category item selected
            self._overview_widget.set_device(None)

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        """Handle search text change."""
        self._refresh_device_list()

    @Slot(int)
    def _on_filter_changed(self, index: int) -> None:
        """Handle category filter change."""
        self._refresh_device_list()

    @Slot(object)
    def _on_device_added(self, device: DeviceInfo) -> None:
        """Handle device added to catalog."""
        self._refresh_device_list()

    @Slot(str)
    def _on_device_removed(self, device_id: str) -> None:
        """Handle device removed from catalog."""
        self._refresh_device_list()

    @Slot(str, object)
    def _on_device_state_changed(self, device_id: str, state) -> None:
        """Handle device state change."""
        # Update the overview if this device is selected
        items = self._device_tree.selectedItems()
        if items:
            selected_id = items[0].data(0, Qt.ItemDataRole.UserRole)
            if selected_id and str(selected_id) == device_id:
                device = self._catalog.get_device(device_id)
                self._overview_widget.set_device(device)

    # === Introspection ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get device panel-specific introspection data."""
        selected_device = None
        items = self._device_tree.selectedItems()
        if items:
            device_id = items[0].data(0, Qt.ItemDataRole.UserRole)
            if device_id:
                device = self._catalog.get_device(device_id)
                if device:
                    selected_device = device.to_summary()

        return {
            "selected_device": selected_device,
            "search_text": self._search_input.text(),
            "category_filter": self._category_filter.currentText(),
            "catalog_info": self._catalog.get_introspection_data(),
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel."""
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "refresh",
                "description": "Refresh the device list",
                "method": "action_refresh",
            },
            {
                "name": "select_device",
                "description": "Select a device by name",
                "method": "action_select_device",
                "parameters": {"name": "string"},
            },
            {
                "name": "search",
                "description": "Search for devices",
                "method": "action_search",
                "parameters": {"query": "string"},
            },
        ])
        return actions

    def action_refresh(self) -> bool:
        """Action: Refresh the device list."""
        self._refresh_device_list()
        return True

    def action_select_device(self, name: str) -> bool:
        """Action: Select a device by name.

        Args:
            name: Device name to select.

        Returns:
            True if device was found and selected.
        """
        # Find and select the device in the tree
        root = self._device_tree.invisibleRootItem()

        for i in range(root.childCount()):
            category_item = root.child(i)
            for j in range(category_item.childCount()):
                device_item = category_item.child(j)
                if device_item.text(0) == name:
                    self._device_tree.setCurrentItem(device_item)
                    return True

        return False

    def action_search(self, query: str) -> bool:
        """Action: Search for devices.

        Args:
            query: Search query string.

        Returns:
            True if search was performed.
        """
        self._search_input.setText(query)
        return True
