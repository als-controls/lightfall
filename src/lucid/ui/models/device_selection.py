"""Device selection model for the device selector dialog.

Provides a checkable tree model of devices and their components,
with a filter proxy for category, writability, kind, and custom filters.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QIcon

if TYPE_CHECKING:
    from lucid.devices.model import DeviceCategory, DeviceInfo


# Category -> QtAwesome icon name (matches device_selector.py)
_CATEGORY_ICON_MAP: dict[str, str] = {
    "motor": "mdi6.engine",
    "detector": "mdi6.camera",
    "controller": "mdi6.tune-variant",
}
_SIGNAL_ICON = "mdi6.signal-variant"

# Lazily populated icon cache
_icon_cache: dict[str, QIcon] = {}


def _get_category_icon(category_value: str | None, node_type: str) -> QIcon | None:
    """Get a cached QtAwesome icon for a device category or signal."""
    if node_type == "signal":
        key = "_signal"
        icon_name = _SIGNAL_ICON
    elif category_value and category_value in _CATEGORY_ICON_MAP:
        key = category_value
        icon_name = _CATEGORY_ICON_MAP[category_value]
    else:
        return None

    if key not in _icon_cache:
        try:
            import qtawesome as qta
            _icon_cache[key] = qta.icon(icon_name)
        except Exception:
            _icon_cache[key] = QIcon()
    return _icon_cache[key]


class DeviceSelectionItem:
    """Item in the device selection tree."""

    __slots__ = (
        "name", "dotted_path", "parent_item", "category", "is_writable",
        "kind", "device_info", "ophyd_obj", "node_type", "check_state", "_children",
    )

    def __init__(
        self, name: str, dotted_path: str, parent: DeviceSelectionItem | None,
        *, category: DeviceCategory | None = None, is_writable: bool = False,
        kind: str | None = None, device_info: DeviceInfo | None = None,
        ophyd_obj: Any = None, node_type: str = "device",
    ) -> None:
        self.name = name
        self.dotted_path = dotted_path
        self.parent_item = parent
        self.category = category
        self.is_writable = is_writable
        self.kind = kind
        self.device_info = device_info
        self.ophyd_obj = ophyd_obj
        self.node_type = node_type
        self.check_state: Qt.CheckState = Qt.CheckState.Unchecked
        self._children: list[DeviceSelectionItem] = []

    @classmethod
    def create_root(cls) -> DeviceSelectionItem:
        return cls(name="", dotted_path="", parent=None)

    def append_child(self, child: DeviceSelectionItem) -> None:
        self._children.append(child)

    def child(self, row: int) -> DeviceSelectionItem | None:
        if 0 <= row < len(self._children):
            return self._children[row]
        return None

    def child_count(self) -> int:
        return len(self._children)

    def row(self) -> int:
        if self.parent_item is not None:
            return self.parent_item._children.index(self)
        return 0

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "dotted_path": self.dotted_path,
            "category": self.category, "is_writable": self.is_writable,
            "kind": self.kind, "device_info": self.device_info,
        }


class DeviceSelectionModel(QAbstractItemModel):
    """Checkable tree model of devices and their components.

    Populates from a device catalog. In flat mode (show_tree=False) only
    top-level devices are shown; in tree mode (show_tree=True) ophyd
    components are recursively added as children.
    """

    DottedPathRole = Qt.ItemDataRole.UserRole + 1
    MetadataDictRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, catalog: Any, show_tree: bool = False, parent: Any = None) -> None:
        super().__init__(parent)
        self._root = DeviceSelectionItem.create_root()
        self._show_tree = show_tree
        self._populate(catalog)

    # ------------------------------------------------------------------
    # Population helpers
    # ------------------------------------------------------------------

    def _populate(self, catalog: Any) -> None:
        self.beginResetModel()
        self._root = DeviceSelectionItem.create_root()
        devices = catalog.get_all_devices()
        for device_info in sorted(devices, key=lambda d: d.name):
            ophyd_obj = getattr(device_info, "_ophyd_device", None) or getattr(device_info, "ophyd_device", None)
            item = DeviceSelectionItem(
                name=device_info.name,
                dotted_path=device_info.name,
                parent=self._root,
                category=device_info.category,
                is_writable=True,  # top-level devices are considered writable
                device_info=device_info,
                ophyd_obj=ophyd_obj,
                node_type="device",
            )
            self._root.append_child(item)
            if self._show_tree and ophyd_obj is not None:
                self._add_components(item, ophyd_obj, parent_path=device_info.name, category=device_info.category)
        self.endResetModel()

    def _add_components(
        self, parent_item: DeviceSelectionItem, ophyd_obj: Any,
        parent_path: str, category: Any,
    ) -> None:
        if not hasattr(ophyd_obj, "component_names"):
            return
        signals = getattr(ophyd_obj, "_signals", {})
        for comp_name in ophyd_obj.component_names:
            comp = signals.get(comp_name)
            if comp is None:
                continue
            dotted = f"{parent_path}.{comp_name}"
            is_writable = self._check_writable(comp)
            kind_str: str | None = None
            try:
                if hasattr(comp, "kind"):
                    kind_str = comp.kind.name.lower()
            except Exception:
                pass
            node_type = "signal"
            if hasattr(comp, "component_names"):
                node_type = "device"
            child = DeviceSelectionItem(
                name=comp_name,
                dotted_path=dotted,
                parent=parent_item,
                category=category,
                is_writable=is_writable,
                kind=kind_str,
                ophyd_obj=comp,
                node_type=node_type,
            )
            parent_item.append_child(child)
            if node_type == "device":
                self._add_components(child, comp, parent_path=dotted, category=category)

    @staticmethod
    def _check_writable(obj: Any) -> bool:
        """Return True if the ophyd object is writable."""
        try:
            meta = getattr(obj, "_metadata", None)
            if meta and isinstance(meta, dict) and "write_access" in meta:
                return bool(meta["write_access"])
        except Exception:
            pass
        # Fallback: class names ending in "RO" are read-only
        cls_name = type(obj).__name__
        if cls_name.endswith("RO"):
            return False
        return True

    # ------------------------------------------------------------------
    # QAbstractItemModel interface
    # ------------------------------------------------------------------

    def index(self, row: int, column: int, parent: QModelIndex | None = None) -> QModelIndex:
        if parent is None:
            parent = QModelIndex()
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_item = self._root if not parent.isValid() else parent.internalPointer()
        child = parent_item.child(row)
        if child is not None:
            return self.createIndex(row, column, child)
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        child_item: DeviceSelectionItem = index.internalPointer()
        parent_item = child_item.parent_item
        if parent_item is None or parent_item is self._root:
            return QModelIndex()
        return self.createIndex(parent_item.row(), 0, parent_item)

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if parent.column() > 0:
            return 0
        parent_item = self._root if not parent.isValid() else parent.internalPointer()
        return parent_item.child_count()

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: ARG002
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        item: DeviceSelectionItem = index.internalPointer()

        if role == Qt.ItemDataRole.DisplayRole:
            return item.name
        elif role == Qt.ItemDataRole.DecorationRole:
            cat_value = item.category.value if item.category else None
            return _get_category_icon(cat_value, item.node_type)
        elif role == Qt.ItemDataRole.CheckStateRole:
            return item.check_state
        elif role == Qt.ItemDataRole.ToolTipRole:
            return item.dotted_path
        elif role == self.DottedPathRole:
            return item.dotted_path
        elif role == self.MetadataDictRole:
            return item.metadata_dict()
        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False
        if role == Qt.ItemDataRole.CheckStateRole:
            item: DeviceSelectionItem = index.internalPointer()
            item.check_state = value
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable

    # ------------------------------------------------------------------
    # Path-based check helpers
    # ------------------------------------------------------------------

    def get_checked_paths(self) -> list[str]:
        """Return dotted paths of all checked items."""
        paths: list[str] = []
        self._collect_checked(self._root, paths)
        return paths

    def _collect_checked(self, item: DeviceSelectionItem, out: list[str]) -> None:
        for i in range(item.child_count()):
            child = item.child(i)
            if child is None:
                continue
            if child.check_state == Qt.CheckState.Checked:
                out.append(child.dotted_path)
            self._collect_checked(child, out)

    def set_checked_paths(self, paths: list[str]) -> None:
        """Check items matching paths; uncheck everything else."""
        path_set = set(paths)
        self._apply_checked(self._root, path_set)

    def _apply_checked(self, item: DeviceSelectionItem, paths: set[str]) -> None:
        for i in range(item.child_count()):
            child = item.child(i)
            if child is None:
                continue
            new_state = Qt.CheckState.Checked if child.dotted_path in paths else Qt.CheckState.Unchecked
            if child.check_state != new_state:
                child.check_state = new_state
                idx = self.createIndex(i, 0, child)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.CheckStateRole])
            self._apply_checked(child, paths)


class DeviceSelectionFilterProxy(QSortFilterProxyModel):
    """Filter/sort proxy for the device selection model."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._categories = None  # set | None
        self._writable_only = False
        self._kinds = None  # set[str] | None
        self._filter_func: Callable | None = None
        self._search_text = ""
        self._sort_key: Callable | None = None
        self.setRecursiveFilteringEnabled(True)

    def _apply_filter(self) -> None:
        self.beginFilterChange()
        self.endFilterChange()

    def set_categories(self, categories: set | None) -> None:
        self._categories = categories
        self._apply_filter()

    def set_writable_only(self, writable_only: bool) -> None:
        self._writable_only = writable_only
        self._apply_filter()

    def set_kinds(self, kinds: set[str] | None) -> None:
        self._kinds = kinds
        self._apply_filter()

    def set_filter_func(self, func: Callable | None) -> None:
        self._filter_func = func
        self._apply_filter()

    def set_search_text(self, text: str) -> None:
        self._search_text = text.lower()
        self._apply_filter()

    def set_sort_key(self, key: Callable | None) -> None:
        self._sort_key = key
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        idx = self.sourceModel().index(source_row, 0, source_parent)
        if not idx.isValid():
            return False
        item: DeviceSelectionItem = idx.internalPointer()
        return self._item_passes(item)

    def _item_passes(self, item: DeviceSelectionItem) -> bool:
        if self._categories is not None and item.category not in self._categories:
            return False
        if self._writable_only and not item.is_writable:
            return False
        if self._kinds is not None and item.kind not in self._kinds:
            return False
        if self._search_text:
            haystack = item.dotted_path.lower()
            desc = ""
            if item.device_info and item.device_info.description:
                desc = item.device_info.description.lower()
            if self._search_text not in haystack and self._search_text not in desc:
                return False
        if self._filter_func is not None and not self._filter_func(item.metadata_dict()):
            return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        if self._sort_key is not None:
            source = self.sourceModel()
            left_meta = source.data(left, DeviceSelectionModel.MetadataDictRole)
            right_meta = source.data(right, DeviceSelectionModel.MetadataDictRole)
            if left_meta is not None and right_meta is not None:
                return self._sort_key(left_meta) < self._sort_key(right_meta)
        left_name = self.sourceModel().data(left, Qt.ItemDataRole.DisplayRole) or ""
        right_name = self.sourceModel().data(right, Qt.ItemDataRole.DisplayRole) or ""
        return left_name < right_name
