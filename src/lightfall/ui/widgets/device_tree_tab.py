"""Device tree tab widget for the Devices panel.

Contains the tree view, toolbar, and search/filter UI previously
housed in DevicePanel directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import qtawesome as qta
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.models.device_tree import (
    DeviceFilterProxyModel,
    DeviceTreeItem,
    DeviceTreeModel,
    NodeType,
)
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.devices.catalog import DeviceCatalog


class DeviceTreeTab(QWidget):
    """The 'All' tab — device tree with toolbar and search/filter.

    Signals:
        device_open_requested: DeviceTreeItem — double-click to open controller.
        favorite_toggled: (device_name, is_favorite) — context menu toggle.
        item_selected: DeviceTreeItem — single selection.
        items_selected: list[DeviceTreeItem] — selection change.
    """

    device_open_requested = Signal(object)
    favorite_toggled = Signal(str, bool)
    item_selected = Signal(object)
    items_selected = Signal(list)

    def __init__(self, catalog: DeviceCatalog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._is_favorite_fn: Any = None

        self._model = DeviceTreeModel(catalog)
        self._proxy_model = DeviceFilterProxyModel()
        self._proxy_model.setSourceModel(self._model)

        self._setup_ui()
        self._connect_signals()

    def set_is_favorite_fn(self, fn) -> None:
        self._is_favorite_fn = fn

    @property
    def model(self) -> DeviceTreeModel:
        return self._model

    @property
    def proxy_model(self) -> DeviceFilterProxyModel:
        return self._proxy_model

    @property
    def tree_view(self) -> QTreeView:
        return self._tree_view

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)

        filter_layout = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search devices and signals...")
        self._search_input.setClearButtonEnabled(True)
        filter_layout.addWidget(self._search_input, stretch=1)

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
        self._proxy_model.set_visible_kinds(default_visible)
        layout.addLayout(filter_layout)

        self._tree_view = QTreeView()
        self._tree_view.setModel(self._proxy_model)
        self._tree_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setAnimated(True)
        self._tree_view.setExpandsOnDoubleClick(False)
        self._tree_view.setSortingEnabled(True)
        self._tree_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        header = self._tree_view.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._tree_view.setColumnWidth(0, 200)

        self._tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree_view.customContextMenuRequested.connect(self._on_context_menu)
        self._tree_view.doubleClicked.connect(self._on_double_clicked)

        layout.addWidget(self._tree_view)
        self._tree_view.collapseAll()

    def _create_toolbar(self) -> QToolBar:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        sync_action = QAction(qta.icon("mdi6.sync"), "Sync", self)
        sync_action.setToolTip(
            "Re-read device backends (e.g. happi JSON), retry failed "
            "connections, and refresh the tree"
        )
        sync_action.triggered.connect(self._sync_devices)
        toolbar.addAction(sync_action)
        toolbar.addSeparator()

        expand_action = QAction(qta.icon("mdi6.arrow-expand-vertical"), "Expand All", self)
        expand_action.triggered.connect(lambda: self._tree_view.expandAll())
        toolbar.addAction(expand_action)

        collapse_action = QAction(qta.icon("mdi6.arrow-collapse-vertical"), "Collapse", self)
        collapse_action.triggered.connect(lambda: self._tree_view.collapseAll())
        toolbar.addAction(collapse_action)
        toolbar.addSeparator()

        self._show_inactive_action = QAction(qta.icon("mdi6.eye-closed"), "Show Disabled", self)
        self._show_inactive_action.setToolTip("Show or hide disabled devices")
        self._show_inactive_action.setCheckable(True)
        self._show_inactive_action.setChecked(False)
        self._show_inactive_action.toggled.connect(self._on_toggle_inactive)
        toolbar.addAction(self._show_inactive_action)

        return toolbar

    def _connect_signals(self) -> None:
        self._search_input.textChanged.connect(self._on_search_changed)
        self._tree_view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._catalog.device_added.connect(self._on_device_changed)
        self._catalog.device_removed.connect(self._on_device_changed)
        # device_updated fires only on explicit metadata edits (edit dialog,
        # active toggle, agent device-tools) — never during the startup
        # connection storm — so refreshing here re-applies the filter (e.g. an
        # active toggle hides/shows the row) with no startup-lag risk. Needed
        # because DeviceFilterProxyModel no longer auto-re-filters on every
        # dataChanged.
        self._catalog.device_updated.connect(self._on_device_changed)

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        self._proxy_model.setFilterRegularExpression(text)
        if text:
            self._tree_view.expandAll()
        else:
            self._tree_view.collapseAll()

    @Slot()
    def _on_kind_filter_changed(self) -> None:
        visible_kinds = {kind for kind, action in self._kind_actions.items() if action.isChecked()}
        if len(visible_kinds) == len(self._kind_actions):
            self._proxy_model.set_visible_kinds(None)
        else:
            self._proxy_model.set_visible_kinds(visible_kinds)
        if visible_kinds and len(visible_kinds) < len(self._kind_actions):
            self._tree_view.expandAll()
        else:
            self._tree_view.collapseAll()

    @Slot(bool)
    def _on_toggle_inactive(self, checked: bool) -> None:
        self._proxy_model.set_show_inactive(checked)
        icon_name = "mdi6.eye" if checked else "mdi6.eye-closed"
        self._show_inactive_action.setIcon(qta.icon(icon_name))

    @Slot()
    def _on_selection_changed(self) -> None:
        selected_items = self._get_selected_items()
        if selected_items:
            self.item_selected.emit(selected_items[0])
        self.items_selected.emit(selected_items)

    def _on_double_clicked(self, proxy_index) -> None:
        source_index = self._proxy_model.mapToSource(proxy_index)
        if not source_index.isValid():
            return
        item = source_index.internalPointer()
        if isinstance(item, DeviceTreeItem) and item.node_type == NodeType.DEVICE:
            self.device_open_requested.emit(item)

    def _on_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QApplication

        index = self._tree_view.indexAt(pos)
        device_info = None
        if index.isValid():
            source_index = self._proxy_model.mapToSource(index)
            if source_index.isValid():
                item = source_index.internalPointer()
                if item is not None and item.node_type == NodeType.DEVICE:
                    device_info = item.device_info

        any_editable = self._get_backend_editable()
        device_editable = self._is_device_editable(device_info) if device_info is not None else False
        menu = QMenu(self._tree_view)

        if device_info is not None:
            # Favorites use device.name as the stable identifier — UUIDs
            # are per-session and would invalidate persisted favorites.
            device_name = device_info.name
            is_fav = self._is_favorite_fn(device_name) if self._is_favorite_fn else False
            if is_fav:
                fav_action = menu.addAction("Remove from Favorites")
                fav_action.triggered.connect(lambda: self.favorite_toggled.emit(device_name, False))
            else:
                fav_action = menu.addAction("Add to Favorites")
                fav_action.triggered.connect(lambda: self.favorite_toggled.emit(device_name, True))
            menu.addSeparator()

            if device_editable:
                edit_action = menu.addAction("Edit...")
                edit_action.triggered.connect(lambda: self._edit_device(device_info))
                if device_info.active:
                    toggle_action = menu.addAction("Disable")
                    toggle_action.triggered.connect(lambda: self._toggle_device_active(device_info, False))
                else:
                    toggle_action = menu.addAction("Enable")
                    toggle_action.triggered.connect(lambda: self._toggle_device_active(device_info, True))
                menu.addSeparator()

            copy_name_action = menu.addAction("Copy Name")
            copy_name_action.triggered.connect(lambda: QApplication.clipboard().setText(device_info.name))
            copy_prefix_action = menu.addAction("Copy Prefix")
            copy_prefix_action.triggered.connect(lambda: QApplication.clipboard().setText(device_info.prefix or ""))

            if device_editable:
                menu.addSeparator()
                delete_action = menu.addAction("Delete")
                delete_action.triggered.connect(lambda: self._delete_device(device_info))
                menu.addSeparator()

        if any_editable:
            add_action = menu.addAction("Add New Device...")
            add_action.triggered.connect(self._add_new_device)

        if not menu.actions():
            return
        menu.exec(self._tree_view.viewport().mapToGlobal(pos))

    def _get_backend_editable(self) -> bool:
        """True if any registered backend supports editing.

        Under a multi-backend configuration (e.g. mock + happi) the primary
        backend may be read-only while a secondary backend is editable. The
        edit actions only need *some* editable backend to be useful.
        """
        return any(b.is_editable for b in self._catalog.backends.values())

    def _is_device_editable(self, device_info) -> bool:
        """True if the backend that owns this specific device is editable.

        Edits route through the owning backend; a device held by a read-only
        backend (e.g. mock) can't be edited even if another backend is.
        """
        if device_info is None:
            return False
        backend = self._catalog._backend_for_device(device_info.id)
        return backend is not None and backend.is_editable

    def _edit_device(self, device_info) -> None:
        from lightfall.ui.dialogs.device_edit_dialog import DeviceEditDialog
        dialog = DeviceEditDialog(mode="edit", device=device_info, parent=self)
        if dialog.exec():
            values = dialog.get_values()
            device_info.display_name = values["display_name"]
            device_info.prefix = values["prefix"]
            device_info.beamline = values["beamline"]
            device_info.group = values["group"]
            device_info.icon_override = values["icon_override"]
            device_info.active = values["active"]
            device_info.metadata.update(values.get("extra_fields", {}))
            self._catalog.update_device(device_info)

    def _add_new_device(self) -> None:
        from lightfall.devices.model import DeviceInfo
        from lightfall.ui.dialogs.device_edit_dialog import DeviceEditDialog
        dialog = DeviceEditDialog(mode="create", parent=self)
        if dialog.exec():
            values = dialog.get_values()
            device = DeviceInfo(
                name=values["name"], device_class=values["device_class"],
                prefix=values["prefix"], beamline=values["beamline"],
                display_name=values["display_name"], group=values["group"],
                icon_override=values["icon_override"], active=values["active"],
                metadata=values.get("extra_fields", {}),
            )
            # Route to the first editable backend rather than the primary, which
            # may be read-only (e.g. mock) in a multi-backend configuration.
            target_backend = next(
                (name for name, b in self._catalog.backends.items() if b.is_editable),
                None,
            )
            if not self._catalog.add_device(device, backend_name=target_backend):
                QMessageBox.warning(self, "Add Failed",
                    f"Failed to add device '{values['name']}'. It may already exist.")

    def _delete_device(self, device_info) -> None:
        reply = QMessageBox.question(self, "Delete Device",
            f"Delete device '{device_info.name}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._catalog.remove_device(device_info.id)

    def _toggle_device_active(self, device_info, active: bool) -> None:
        device_info.active = active
        self._catalog.update_device(device_info)

    def _sync_devices(self) -> None:
        from lightfall.utils.threads import QThreadFuture
        # Pick up out-of-band edits to the backing store (e.g. someone
        # edited the happi JSON) before retrying connections. Without
        # this, Sync only reconnected the existing cached entries.
        self._catalog.reload_backends()
        for backend in self._catalog.backends.values():
            if hasattr(backend, "reset_failed_devices"):
                backend.reset_failed_devices()
        def _do_reconnect():
            return self._catalog.reconnect_failed_devices(timeout=5.0)
        def _on_done(result):
            connected, failed = result
            self._model.refresh()
            logger.info("Sync: {} connected, {} still offline", connected, failed)
        thread = QThreadFuture(_do_reconnect, callback_slot=_on_done, name="sync-devices")
        thread.start()
        self._model.refresh()
        logger.info("Syncing devices...")

    def _on_device_changed(self, _: Any) -> None:
        self._model.refresh()

    def _get_selected_items(self) -> list[DeviceTreeItem]:
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

    def select_device_by_id(self, device_id: str) -> None:
        root_item = self._model.root_item
        target_item = self._find_device_item(root_item, device_id)
        if target_item is None:
            return
        source_index = self._model.index_for_item(target_item)
        if not source_index.isValid():
            return
        proxy_index = self._proxy_model.mapFromSource(source_index)
        if not proxy_index.isValid():
            return
        # Block signals to prevent recursive events during programmatic selection
        self._tree_view.selectionModel().blockSignals(True)
        try:
            self._tree_view.selectionModel().clearSelection()
            self._tree_view.selectionModel().select(
                proxy_index,
                self._tree_view.selectionModel().SelectionFlag.Select
                | self._tree_view.selectionModel().SelectionFlag.Rows,
            )
            self._tree_view.scrollTo(proxy_index)
            self._tree_view.expand(proxy_index.parent())
        finally:
            self._tree_view.selectionModel().blockSignals(False)

        # Force immediate visual update
        self._tree_view.viewport().update()

    def _find_device_item(self, item: DeviceTreeItem, device_id: str) -> DeviceTreeItem | None:
        if item.device_info is not None and str(item.device_info.id) == device_id:
            return item
        for i in range(item.child_count()):
            child = item.child(i)
            if child:
                result = self._find_device_item(child, device_id)
                if result:
                    return result
        return None

    def get_visible_kinds(self) -> set[str] | None:
        return self._proxy_model.get_visible_kinds()

    def set_visible_kinds(self, kinds: list[str] | None) -> None:
        """Set the visible kinds filter.

        Args:
            kinds: List of kind names to show, or None to show all.
        """
        if kinds is None:
            for action in self._kind_actions.values():
                action.setChecked(True)
        else:
            for kind, action in self._kind_actions.items():
                action.setChecked(kind in kinds)
        self._on_kind_filter_changed()

    def get_search_text(self) -> str:
        return self._search_input.text()

    def set_search_text(self, query: str) -> None:
        """Set the search query text.

        Args:
            query: Search query string.
        """
        self._search_input.setText(query)

    def find_item_by_id(self, device_id: str) -> DeviceTreeItem | None:
        """Find a device tree item by device ID.

        Args:
            device_id: The device ID string.

        Returns:
            The matching DeviceTreeItem, or None if not found.
        """
        return self._find_device_item(self._model.root_item, device_id)
