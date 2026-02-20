"""Device tree model for Qt Model/View architecture.

Provides a hierarchical model of ophyd devices showing the actual
device/signal tree structure.
"""

from __future__ import annotations

import inspect
from enum import Enum
from typing import TYPE_CHECKING, Any

from loguru import logger
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QSortFilterProxyModel,
    QTimer,
    Qt,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

if TYPE_CHECKING:

    from lucid.devices import DeviceCatalog, DeviceInfo


class NodeType(Enum):
    """Type of node in the device tree."""

    ROOT = "root"
    DEVICE = "device"
    SIGNAL = "signal"
    COMPONENT = "component"


class DeviceTreeItem:
    """Item in the device tree model.

    Represents either a device or a signal/component in the
    ophyd device hierarchy.
    """

    def __init__(
        self,
        name: str,
        node_type: NodeType,
        parent: DeviceTreeItem | None = None,
        ophyd_obj: Any = None,
        device_info: DeviceInfo | None = None,
    ) -> None:
        """Initialize a tree item.

        Args:
            name: Display name for this item.
            node_type: Type of node (device, signal, etc.).
            parent: Parent item in the tree.
            ophyd_obj: The ophyd object (Device or Signal).
            device_info: DeviceInfo from the catalog (for top-level devices).
        """
        self.name = name
        self.node_type = node_type
        self.parent_item = parent
        self.ophyd_obj = ophyd_obj
        self.device_info = device_info
        self.children: list[DeviceTreeItem] = []

        # Cache for display values (updated by background thread)
        self._cached_value: str = ""
        self._status_cache: str = ""

    def append_child(self, child: DeviceTreeItem) -> None:
        """Add a child item."""
        self.children.append(child)

    def child(self, row: int) -> DeviceTreeItem | None:
        """Get child at row."""
        if 0 <= row < len(self.children):
            return self.children[row]
        return None

    def child_count(self) -> int:
        """Get number of children."""
        return len(self.children)

    def row(self) -> int:
        """Get this item's row in its parent."""
        if self.parent_item:
            return self.parent_item.children.index(self)
        return 0

    def column_count(self) -> int:
        """Number of columns."""
        return 5  # Name, Value, Type, Kind, Status

    def data(self, column: int) -> Any:
        """Get data for column."""
        if column == 0:
            return self.name
        elif column == 1:
            return self._cached_value
        elif column == 2:
            return self._get_type_string()
        elif column == 3:
            return self._get_kind_string()
        elif column == 4:
            return self._get_status()
        return None

    def _get_units(self) -> str:
        """Get the engineering units string for this object.

        Checks ophyd metadata, .egu attribute, and device_info metadata.
        """
        obj = self.ophyd_obj
        if obj is None:
            return ""

        # ophyd signals store units in .metadata or .egu
        try:
            if hasattr(obj, "metadata") and isinstance(obj.metadata, dict):
                units = obj.metadata.get("units", "")
                if units:
                    return str(units)
        except Exception:
            pass

        try:
            if hasattr(obj, "egu"):
                egu = obj.egu
                if egu:
                    return str(egu)
        except Exception:
            pass

        # For devices, check readback signal's units
        if self.node_type == NodeType.DEVICE:
            for attr in ("readback", "user_readback"):
                try:
                    sig = getattr(obj, attr, None)
                    if sig is not None and hasattr(sig, "metadata"):
                        units = sig.metadata.get("units", "")
                        if units:
                            return str(units)
                except Exception:
                    pass

        # Fallback to device_info metadata
        if self.device_info and self.device_info.metadata:
            units = self.device_info.metadata.get("units", "")
            if units:
                return str(units)

        return ""

    def _format_value(self, val: Any) -> str:
        """Format a value with units."""
        if isinstance(val, float):
            text = f"{val:.4g}"
        else:
            text = str(val)

        units = self._get_units()
        if units:
            return f"{text} {units}"
        return text

    @staticmethod
    def _safe_get(obj: Any) -> Any:
        """Get value from an object, handling async .get() methods.

        Prefers get_sync() if available, otherwise calls get() and
        handles the case where it returns a coroutine.
        """
        # Prefer synchronous getter if available
        if hasattr(obj, "get_sync"):
            return obj.get_sync()

        if hasattr(obj, "get"):
            result = obj.get()
            # If get() returned a coroutine, close it to avoid warnings
            if inspect.iscoroutine(result):
                result.close()
                return None
            return result

        return None

    def refresh_cached_value(self) -> bool:
        """Recompute the cached value string. Returns True if it changed.

        Safe to call from a background thread — only reads from ophyd objects.
        """
        new_value = self._get_value()
        if new_value != self._cached_value:
            self._cached_value = new_value
            return True
        return False

    def _get_value(self) -> str:
        """Get current value as string with units.

        Only shows values for:
        - Signals (leaf nodes)
        - Devices with a 'readback' component (shows the readback value)
        """
        if self.ophyd_obj is None:
            return ""

        try:
            # For signals, show their value directly
            if self.node_type == NodeType.SIGNAL:
                val = self._safe_get(self.ophyd_obj)
                if val is not None:
                    return self._format_value(val)

            # For devices, show value from readback, position, or direct .get()
            elif self.node_type == NodeType.DEVICE:
                if hasattr(self.ophyd_obj, "readback"):
                    val = self._safe_get(self.ophyd_obj.readback)
                    if val is not None:
                        return self._format_value(val)
                # Check for position (motors) — uses cached _position
                # (updated by init, move(), or read())
                elif hasattr(self.ophyd_obj, "position"):
                    return self._format_value(self.ophyd_obj.position)
                # Fallback: if the device itself is signal-like (e.g. EpicsSignal)
                elif hasattr(self.ophyd_obj, "get") and not hasattr(
                    self.ophyd_obj, "component_names"
                ):
                    val = self._safe_get(self.ophyd_obj)
                    if val is not None:
                        return self._format_value(val)
        except Exception:
            pass
        return ""

    def _get_type_string(self) -> str:
        """Get type description (actual class name)."""
        if self.ophyd_obj is None:
            return ""

        return type(self.ophyd_obj).__name__

    def _get_kind_string(self) -> str:
        """Get the ophyd Kind as a string."""
        if self.ophyd_obj is None:
            return ""

        try:
            if hasattr(self.ophyd_obj, "kind"):
                return self.ophyd_obj.kind.name
        except Exception:
            pass
        return ""

    def get_kind(self) -> str | None:
        """Get the ophyd Kind name for filtering.

        Returns:
            Kind name (hinted, normal, config, omitted) or None.
        """
        if self.ophyd_obj is None:
            return None

        try:
            if hasattr(self.ophyd_obj, "kind"):
                return self.ophyd_obj.kind.name
        except Exception:
            pass
        return None

    def _get_status(self) -> str:
        """Get status string."""
        if self.device_info and self.device_info.state:
            return self.device_info.state.status.value
        if self.ophyd_obj is not None:
            return "connected"
        return ""

    def get_device_category(self) -> str:
        """Get device category for icon selection."""
        if self.device_info:
            return self.device_info.category.value

        if self.ophyd_obj is None:
            return "other"

        cls_name = type(self.ophyd_obj).__name__.lower()

        if "motor" in cls_name or "axis" in cls_name or "positioner" in cls_name:
            return "motor"
        elif "detector" in cls_name or "gauss" in cls_name:
            return "detector"
        elif "camera" in cls_name or "img" in cls_name or "image" in cls_name:
            return "camera"
        elif "signal" in cls_name:
            return "signal"

        # Check by ophyd base classes
        try:
            from ophyd import Signal

            if isinstance(self.ophyd_obj, Signal):
                return "signal"
        except ImportError:
            pass

        return "device"


class DeviceTreeModel(QAbstractItemModel):
    """Qt model for ophyd device tree hierarchy.

    This model displays the actual ophyd device structure with
    parent/child relationships. Top-level items are devices from
    the DeviceCatalog, and children are their components (signals
    or sub-devices).

    Columns:
        0: Name - device/signal name
        1: Value - current value (for signals) or position (for motors)
        2: Type - device class type
        3: Kind - ophyd Kind (hinted, normal, config, omitted)
        4: Status - connection status
    """

    COLUMNS = ["Name", "Value", "Type", "Kind", "Status"]

    def __init__(
        self,
        catalog: DeviceCatalog,
        parent: Any = None,
    ) -> None:
        """Initialize the model.

        Args:
            catalog: Device catalog to populate from.
            parent: Qt parent.
        """
        super().__init__(parent)
        self._catalog = catalog
        self._root = DeviceTreeItem("root", NodeType.ROOT)
        self._icons: dict[str, QIcon] = {}

        self._create_icons()
        self._populate()

        # Background value refresh: fetch values off the main thread
        self._value_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dev-values")
        self._value_future: Future | None = None

        self._value_timer = QTimer(self)
        self._value_timer.timeout.connect(self._poll_value_refresh)
        self._value_timer.start(2000)  # Every 2 seconds

    def _create_icons(self) -> None:
        """Create icons for device types."""
        icon_specs = {
            "motor": ("#4CAF50", "M"),  # Green
            "detector": ("#2196F3", "D"),  # Blue
            "camera": ("#9C27B0", "C"),  # Purple
            "sensor": ("#FF9800", "S"),  # Orange
            "signal": ("#607D8B", "s"),  # Gray
            "device": ("#795548", "d"),  # Brown
            "other": ("#9E9E9E", "?"),  # Gray
        }

        for name, (color, letter) in icon_specs.items():
            self._icons[name] = self._create_letter_icon(color, letter)

    def _create_letter_icon(self, color: str, letter: str) -> QIcon:
        """Create a simple colored icon with a letter."""
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw colored circle
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, size - 2, size - 2)

        # Draw letter
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(10)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, letter)

        painter.end()

        return QIcon(pixmap)

    def _populate(self) -> None:
        """Populate the model from the catalog."""
        self.beginResetModel()
        self._root.children.clear()

        # Get all devices from catalog
        devices = self._catalog.get_all_devices()

        for device_info in sorted(devices, key=lambda d: d.name):
            device_item = self._create_device_item(device_info)
            if device_item:
                self._root.append_child(device_item)

        self.endResetModel()
        logger.debug("Populated device tree with {} top-level devices", len(self._root.children))

    def _create_device_item(self, device_info: DeviceInfo) -> DeviceTreeItem | None:
        """Create a tree item for a device and its components."""
        ophyd_device = device_info.ophyd_device

        item = DeviceTreeItem(
            name=device_info.name,
            node_type=NodeType.DEVICE,
            parent=self._root,
            ophyd_obj=ophyd_device,
            device_info=device_info,
        )

        # Add components as children
        if ophyd_device is not None:
            self._add_components(item, ophyd_device)

        return item

    def _add_components(self, parent_item: DeviceTreeItem, ophyd_obj: Any) -> None:
        """Recursively add components as children."""
        try:
            # Get component names
            if not hasattr(ophyd_obj, "component_names"):
                return

            for comp_name in ophyd_obj.component_names:
                try:
                    comp = getattr(ophyd_obj, comp_name)
                except Exception:
                    continue

                # Determine if this is a device or signal
                is_device = False
                try:
                    from ophyd import Device

                    is_device = isinstance(comp, Device)
                except ImportError:
                    is_device = hasattr(comp, "component_names")

                node_type = NodeType.DEVICE if is_device else NodeType.SIGNAL

                child_item = DeviceTreeItem(
                    name=comp_name,
                    node_type=node_type,
                    parent=parent_item,
                    ophyd_obj=comp,
                )
                parent_item.append_child(child_item)

                # Recursively add sub-components for devices
                if is_device:
                    self._add_components(child_item, comp)

        except Exception as e:
            logger.debug("Error adding components for {}: {}", parent_item.name, e)

    def _poll_value_refresh(self) -> None:
        """Timer callback: check if background fetch is done, then emit updates."""
        if self._value_future is not None:
            if not self._value_future.done():
                return  # Still working, skip this tick
            # Fetch completed — emit dataChanged for value column
            self._value_future = None
            self._emit_value_changed()

        # Start a new background fetch
        if self._root.children:
            self._value_future = self._value_executor.submit(self._fetch_all_values)

    def _fetch_all_values(self) -> None:
        """Background thread: refresh cached values on all tree items."""
        self._fetch_item_values(self._root)

    def _fetch_item_values(self, item: DeviceTreeItem) -> None:
        """Recursively refresh cached values for an item and its children."""
        if item is not self._root:
            try:
                item.refresh_cached_value()
            except Exception:
                pass

        for child in item.children:
            self._fetch_item_values(child)

    def _emit_value_changed(self) -> None:
        """Emit dataChanged for the Value column across all rows."""
        if not self._root.children:
            return
        top_left = self.index(0, 1)
        bottom_right = self.index(self._root.child_count() - 1, 1)
        self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole])

        for row in range(self._root.child_count()):
            parent_index = self.index(row, 0)
            child_count = self.rowCount(parent_index)
            if child_count > 0:
                child_top = self.index(0, 1, parent_index)
                child_bottom = self.index(child_count - 1, 1, parent_index)
                self.dataChanged.emit(child_top, child_bottom, [Qt.ItemDataRole.DisplayRole])

    def refresh(self) -> None:
        """Refresh the model from the catalog."""
        self._populate()

    def get_icon(self, category: str) -> QIcon:
        """Get icon for a device category."""
        return self._icons.get(category, self._icons["other"])

    @property
    def root_item(self) -> DeviceTreeItem:
        """Get the root item of the tree."""
        return self._root

    def index_for_item(self, item: DeviceTreeItem) -> QModelIndex:
        """Get model index for a tree item.

        Args:
            item: The tree item to find.

        Returns:
            QModelIndex for the item, or invalid index if not found.
        """
        if item is None or item is self._root:
            return QModelIndex()

        # Get the item's row within its parent
        parent_item = item.parent_item
        if parent_item is None:
            return QModelIndex()

        row = item.row()
        if row < 0:
            return QModelIndex()

        return self.createIndex(row, 0, item)

    # === QAbstractItemModel implementation ===

    def index(
        self, row: int, column: int, parent: QModelIndex | None = None
    ) -> QModelIndex:
        """Create index for item at row, column under parent."""
        if parent is None:
            parent = QModelIndex()
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        if not parent.isValid():
            parent_item = self._root
        else:
            parent_item = parent.internalPointer()

        child_item = parent_item.child(row)
        if child_item:
            return self.createIndex(row, column, child_item)

        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        """Get parent index of item."""
        if not index.isValid():
            return QModelIndex()

        child_item: DeviceTreeItem = index.internalPointer()
        parent_item = child_item.parent_item

        if parent_item is None or parent_item is self._root:
            return QModelIndex()

        return self.createIndex(parent_item.row(), 0, parent_item)

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """Get number of rows under parent."""
        if parent is None:
            parent = QModelIndex()
        if parent.column() > 0:
            return 0

        if not parent.isValid():
            parent_item = self._root
        else:
            parent_item = parent.internalPointer()

        return parent_item.child_count()

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: ARG002
        """Get number of columns."""
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Get data for index and role."""
        if not index.isValid():
            return None

        item: DeviceTreeItem = index.internalPointer()

        if role == Qt.ItemDataRole.DisplayRole:
            return item.data(index.column())

        elif role == Qt.ItemDataRole.DecorationRole:
            if index.column() == 0:
                category = item.get_device_category()
                return self.get_icon(category)

        elif role == Qt.ItemDataRole.ForegroundRole:
            # Color status column
            if index.column() == 4:
                status = item.data(4)
                if status == "online" or status == "connected":
                    return QColor("#4CAF50")  # Green
                elif status == "error":
                    return QColor("#F44336")  # Red
                elif status == "offline":
                    return QColor("#9E9E9E")  # Gray
            # Color kind column
            elif index.column() == 3:
                kind = item.data(3)
                if kind == "hinted":
                    return QColor("#4CAF50")  # Green - important
                elif kind == "config":
                    return QColor("#FF9800")  # Orange - configuration
                elif kind == "omitted":
                    return QColor("#9E9E9E")  # Gray - hidden

        elif role == Qt.ItemDataRole.UserRole:
            # Return the ophyd object
            return item.ophyd_obj

        elif role == Qt.ItemDataRole.UserRole + 1:
            # Return the device info
            return item.device_info

        elif role == Qt.ItemDataRole.UserRole + 2:
            # Return the node type
            return item.node_type

        elif role == Qt.ItemDataRole.UserRole + 3:
            # Return the kind string for filtering
            return item.get_kind()

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Get header data."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Get item flags."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class DeviceFilterProxyModel(QSortFilterProxyModel):
    """Filter proxy for searching devices.

    Filters the device tree by name and/or kind, showing matching items
    and their parents (to maintain tree structure).
    """

    # Role for accessing kind data
    KIND_ROLE = Qt.ItemDataRole.UserRole + 3

    def __init__(self, parent: Any = None) -> None:
        """Initialize the filter proxy."""
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        # Kind filter: set of kinds to show (None means show all)
        self._visible_kinds: set[str] | None = None

    def set_visible_kinds(self, kinds: set[str] | None) -> None:
        """Set which kinds should be visible.

        Args:
            kinds: Set of kind names to show (hinted, normal, config, omitted),
                   or None to show all kinds.
        """
        self._visible_kinds = kinds
        self.invalidateFilter()

    def get_visible_kinds(self) -> set[str] | None:
        """Get the currently visible kinds."""
        return self._visible_kinds

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex
    ) -> bool:
        """Check if row should be shown.

        Shows items that match both text filter and kind filter,
        or have descendants that match.
        """
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)

        # Check text filter
        pattern = self.filterRegularExpression().pattern()
        text_matches = not pattern or self._item_matches_text(index, pattern)

        # Check kind filter
        kind_matches = self._item_matches_kind(index)

        # For top-level devices (no kind), always show if they have matching children
        item_kind = index.data(self.KIND_ROLE)

        if item_kind is None:
            # This is likely a top-level device - show if any descendant matches
            if self._has_matching_descendant(index, pattern):
                return True
            # If no text pattern, show top-level devices
            return not pattern

        # For items with kind, must match both filters
        if text_matches and kind_matches:
            return True

        # Check if any descendant matches
        return self._has_matching_descendant(index, pattern)

    def _item_matches_text(self, index: QModelIndex, pattern: str) -> bool:
        """Check if item matches the text filter pattern."""
        if not index.isValid():
            return False

        # Check name (column 0)
        name = index.data(Qt.ItemDataRole.DisplayRole)
        if name and pattern.lower() in name.lower():
            return True

        # Check type (column 2)
        source_model = self.sourceModel()
        type_index = source_model.index(index.row(), 2, index.parent())
        type_str = type_index.data(Qt.ItemDataRole.DisplayRole)
        if type_str and pattern.lower() in type_str.lower():
            return True

        return False

    def _item_matches_kind(self, index: QModelIndex) -> bool:
        """Check if item matches the kind filter."""
        if self._visible_kinds is None:
            return True  # No filter, show all

        item_kind = index.data(self.KIND_ROLE)

        # Items without kind always pass kind filter
        if item_kind is None:
            return True

        # Handle compound kind strings like "normal|config" — match if
        # ANY component is in the visible set
        kind_parts = {k.strip() for k in item_kind.split("|")} if "|" in item_kind else {item_kind}
        return bool(kind_parts & self._visible_kinds)

    def _has_matching_descendant(self, index: QModelIndex, pattern: str) -> bool:
        """Check if any descendant matches both text and kind filters."""
        source_model = self.sourceModel()
        rows = source_model.rowCount(index)

        for row in range(rows):
            child_index = source_model.index(row, 0, index)

            # Check if child matches both filters
            text_matches = not pattern or self._item_matches_text(child_index, pattern)
            kind_matches = self._item_matches_kind(child_index)

            if text_matches and kind_matches:
                return True

            # Recursively check descendants
            if self._has_matching_descendant(child_index, pattern):
                return True

        return False
