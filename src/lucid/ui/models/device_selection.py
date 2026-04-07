"""Device selection model for the device selector dialog.

Provides a checkable tree model of devices and their components,
with a filter proxy for category, writability, kind, and custom filters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt

if TYPE_CHECKING:
    from lucid.devices.model import DeviceCategory, DeviceInfo


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
