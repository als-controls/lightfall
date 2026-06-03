# Device Selector Dialog v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat QListWidget device selector with a tree-aware QTreeView dialog backed by proper Qt models, supporting component selection, category/writable/kind filtering, and QtAwesome button icons.

**Architecture:** New `DeviceSelectionModel` (QAbstractItemModel) builds a checkable tree from DeviceCatalog. `DeviceSelectionFilterProxy` (QSortFilterProxyModel) handles all filtering/sorting. `DeviceSelectorDialog` uses QTreeView with the model stack. `DeviceParameterItem` gets a QtAwesome icon button. Annotations updated with `DeviceIcon` and multi-category `DeviceFilter`.

**Tech Stack:** PySide6 (Qt model/view), pyqtgraph (ParameterTree), QtAwesome (icons), ophyd (device introspection)

**Spec:** `docs/superpowers/specs/2026-04-06-device-selector-v2-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/lightfall/ui/models/device_selection.py` | **Create** | `DeviceSelectionItem`, `DeviceSelectionModel`, `DeviceSelectionFilterProxy` |
| `src/lightfall/ui/widgets/device_selector.py` | **Rewrite** | `DeviceSelectorDialog` (QTreeView-based), `DeviceParameterItem`, `DeviceParameter` |
| `src/lightfall/ui/annotations.py` | **Modify** | Add `DeviceIcon`, update `DeviceFilter.category` type |
| `src/lightfall/ui/widgets/plan_config.py` | **Modify** | Update `_build_param_spec` to translate annotations to new dialog params |
| `tests/test_device_selection_model.py` | **Create** | Tests for model, filter proxy, writable detection |
| `tests/test_device_selector_dialog.py` | **Create** | Tests for dialog, parameter item, icon resolution |

---

## Task 1: Add `DeviceIcon` Annotation and Update `DeviceFilter`

**Files:**
- Modify: `src/lightfall/ui/annotations.py`
- Test: `tests/test_annotations.py`

- [ ] **Step 1: Write tests for new annotations**

Add to the end of `tests/test_annotations.py`:

```python
class TestDeviceIcon:
    """Tests for DeviceIcon annotation."""

    def test_device_icon_stores_name(self):
        """DeviceIcon stores the icon name."""
        from lightfall.ui.annotations import DeviceIcon

        icon = DeviceIcon("mdi6.engine")
        assert icon.name == "mdi6.engine"

    def test_device_icon_is_frozen(self):
        """DeviceIcon is immutable."""
        from lightfall.ui.annotations import DeviceIcon

        icon = DeviceIcon("mdi6.engine")
        with pytest.raises(AttributeError):
            icon.name = "other"


class TestDeviceFilterMultiCategory:
    """Tests for DeviceFilter multi-category support."""

    def test_category_as_string(self):
        """DeviceFilter.category accepts a string (backwards compatible)."""
        from lightfall.ui.annotations import DeviceFilter

        flt = DeviceFilter(category="motor")
        assert flt.category == "motor"

    def test_category_as_set(self):
        """DeviceFilter.category accepts a set of strings."""
        from lightfall.ui.annotations import DeviceFilter

        flt = DeviceFilter(category={"motor", "controller"})
        assert flt.category == {"motor", "controller"}

    def test_category_default_none(self):
        """DeviceFilter.category defaults to None."""
        from lightfall.ui.annotations import DeviceFilter

        flt = DeviceFilter()
        assert flt.category is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_annotations.py::TestDeviceIcon tests/test_annotations.py::TestDeviceFilterMultiCategory -v`
Expected: FAIL — `DeviceIcon` not importable; multi-category tests pass already since `category` accepts any value in frozen dataclass.

- [ ] **Step 3: Add DeviceIcon and update DeviceFilter**

In `src/lightfall/ui/annotations.py`, add after the `DeviceDefault` class (before `__all__`):

```python
@dataclass(frozen=True)
class DeviceIcon:
    """QtAwesome icon identifier for the device parameter button.

    Specifies which icon to show on the device selector button in the
    plan configuration UI. If the icon string has no dot prefix,
    ``mdi6.`` is prepended automatically.

    Args:
        name: QtAwesome icon identifier (e.g., ``"mdi6.engine"``, ``"camera"``).

    Example:
        motor: Annotated[Device, DeviceFilter(category="motor"), DeviceIcon("engine")]
    """

    name: str
```

Update `DeviceFilter.category` type annotation:

```python
    category: str | set[str] | None = None
```

Add `"DeviceIcon"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_annotations.py::TestDeviceIcon tests/test_annotations.py::TestDeviceFilterMultiCategory -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lightfall/ui/annotations.py tests/test_annotations.py
git commit -m "feat: add DeviceIcon annotation, multi-category DeviceFilter"
```

---

## Task 2: Create `DeviceSelectionItem` Data Class

**Files:**
- Create: `src/lightfall/ui/models/device_selection.py`
- Create: `tests/test_device_selection_model.py`

- [ ] **Step 1: Write tests for DeviceSelectionItem**

Create `tests/test_device_selection_model.py`:

```python
"""Tests for DeviceSelectionModel and DeviceSelectionFilterProxy."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from lightfall.ui.models.device_selection import DeviceSelectionItem


class TestDeviceSelectionItem:
    """Tests for the tree item data class."""

    def test_root_item(self):
        """Root item has no parent and empty path."""
        root = DeviceSelectionItem.create_root()
        assert root.name == ""
        assert root.dotted_path == ""
        assert root.parent_item is None
        assert root.child_count() == 0

    def test_append_child(self):
        """Children are appended and parent is set."""
        root = DeviceSelectionItem.create_root()
        child = DeviceSelectionItem(
            name="motor1",
            dotted_path="motor1",
            parent=root,
        )
        root.append_child(child)
        assert root.child_count() == 1
        assert root.child(0) is child
        assert child.parent_item is root
        assert child.row() == 0

    def test_dotted_path_for_nested(self):
        """Nested items have dotted paths."""
        root = DeviceSelectionItem.create_root()
        device = DeviceSelectionItem(
            name="motor1", dotted_path="motor1", parent=root,
        )
        root.append_child(device)
        signal = DeviceSelectionItem(
            name="readback", dotted_path="motor1.readback", parent=device,
        )
        device.append_child(signal)
        assert signal.dotted_path == "motor1.readback"
        assert signal.row() == 0

    def test_check_state_default_unchecked(self):
        """Items start unchecked."""
        item = DeviceSelectionItem(name="x", dotted_path="x", parent=None)
        assert item.check_state == Qt.CheckState.Unchecked

    def test_check_state_toggle(self):
        """Check state can be changed independently."""
        root = DeviceSelectionItem.create_root()
        parent = DeviceSelectionItem(name="dev", dotted_path="dev", parent=root)
        child = DeviceSelectionItem(name="sig", dotted_path="dev.sig", parent=parent)
        root.append_child(parent)
        parent.append_child(child)

        child.check_state = Qt.CheckState.Checked
        assert child.check_state == Qt.CheckState.Checked
        assert parent.check_state == Qt.CheckState.Unchecked

    def test_is_writable_default(self):
        """Items default to not writable."""
        item = DeviceSelectionItem(name="x", dotted_path="x", parent=None)
        assert item.is_writable is False

    def test_metadata_dict(self):
        """metadata_dict returns filterable dict."""
        from lightfall.devices.model import DeviceCategory

        item = DeviceSelectionItem(
            name="motor1",
            dotted_path="motor1",
            parent=None,
            category=DeviceCategory.MOTOR,
            is_writable=True,
            kind="hinted",
        )
        d = item.metadata_dict()
        assert d["name"] == "motor1"
        assert d["dotted_path"] == "motor1"
        assert d["category"] == DeviceCategory.MOTOR
        assert d["is_writable"] is True
        assert d["kind"] == "hinted"
        assert d["device_info"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_selection_model.py::TestDeviceSelectionItem -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement DeviceSelectionItem**

Create `src/lightfall/ui/models/device_selection.py`:

```python
"""Device selection model for the device selector dialog.

Provides a checkable tree model of devices and their components,
with a filter proxy for category, writability, kind, and custom filters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt

if TYPE_CHECKING:
    from lightfall.devices.model import DeviceCategory, DeviceInfo


class DeviceSelectionItem:
    """Item in the device selection tree.

    Stores device/signal metadata and check state for the selection model.
    """

    __slots__ = (
        "name",
        "dotted_path",
        "parent_item",
        "category",
        "is_writable",
        "kind",
        "device_info",
        "ophyd_obj",
        "node_type",
        "check_state",
        "_children",
    )

    def __init__(
        self,
        name: str,
        dotted_path: str,
        parent: DeviceSelectionItem | None,
        *,
        category: DeviceCategory | None = None,
        is_writable: bool = False,
        kind: str | None = None,
        device_info: DeviceInfo | None = None,
        ophyd_obj: Any = None,
        node_type: str = "device",
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
        """Create an invisible root item."""
        return cls(name="", dotted_path="", parent=None)

    def append_child(self, child: DeviceSelectionItem) -> None:
        """Add a child item."""
        self._children.append(child)

    def child(self, row: int) -> DeviceSelectionItem | None:
        """Get child at row index."""
        if 0 <= row < len(self._children):
            return self._children[row]
        return None

    def child_count(self) -> int:
        """Number of children."""
        return len(self._children)

    def row(self) -> int:
        """Row index within parent."""
        if self.parent_item is not None:
            return self.parent_item._children.index(self)
        return 0

    def metadata_dict(self) -> dict[str, Any]:
        """Return a dict of metadata for use by filter/sort functions."""
        return {
            "name": self.name,
            "dotted_path": self.dotted_path,
            "category": self.category,
            "is_writable": self.is_writable,
            "kind": self.kind,
            "device_info": self.device_info,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_selection_model.py::TestDeviceSelectionItem -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lightfall/ui/models/device_selection.py tests/test_device_selection_model.py
git commit -m "feat: add DeviceSelectionItem for device selector tree"
```

---

## Task 3: Create `DeviceSelectionModel`

**Files:**
- Modify: `src/lightfall/ui/models/device_selection.py`
- Modify: `tests/test_device_selection_model.py`

- [ ] **Step 1: Write tests for DeviceSelectionModel**

Append to `tests/test_device_selection_model.py`:

```python
from unittest.mock import MagicMock

from lightfall.devices.model import DeviceCategory, DeviceInfo
from lightfall.ui.models.device_selection import DeviceSelectionModel


def _make_device_info(name: str, category: DeviceCategory = DeviceCategory.MOTOR) -> DeviceInfo:
    """Helper to create a DeviceInfo with a mock ophyd device."""
    from ophyd.sim import SynAxis, SynSignal

    if category == DeviceCategory.MOTOR:
        ophyd_dev = SynAxis(name=name)
    else:
        ophyd_dev = SynSignal(name=name, func=lambda: 1.0)

    info = DeviceInfo(name=name, category=category)
    info._ophyd_device = ophyd_dev
    return info


def _make_catalog(devices: list[DeviceInfo]) -> MagicMock:
    """Create a mock DeviceCatalog."""
    catalog = MagicMock()
    catalog.get_all_devices.return_value = devices
    return catalog


class TestDeviceSelectionModel:
    """Tests for the Qt tree model."""

    def test_empty_catalog(self, qapp):
        """Empty catalog produces empty model."""
        catalog = _make_catalog([])
        model = DeviceSelectionModel(catalog)
        assert model.rowCount() == 0

    def test_flat_mode_no_children(self, qapp):
        """show_tree=False shows only top-level devices."""
        devices = [_make_device_info("motor1")]
        catalog = _make_catalog(devices)
        model = DeviceSelectionModel(catalog, show_tree=False)

        assert model.rowCount() == 1
        idx = model.index(0, 0)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "motor1"
        assert model.rowCount(idx) == 0  # No children

    def test_tree_mode_has_children(self, qapp):
        """show_tree=True populates component children."""
        devices = [_make_device_info("motor1", DeviceCategory.MOTOR)]
        catalog = _make_catalog(devices)
        model = DeviceSelectionModel(catalog, show_tree=True)

        root_idx = model.index(0, 0)
        assert model.data(root_idx, Qt.ItemDataRole.DisplayRole) == "motor1"
        # SynAxis has components: readback, setpoint, velocity, acceleration, unused
        assert model.rowCount(root_idx) >= 2

    def test_dotted_path_role(self, qapp):
        """DottedPathRole returns the full dotted path."""
        devices = [_make_device_info("motor1", DeviceCategory.MOTOR)]
        catalog = _make_catalog(devices)
        model = DeviceSelectionModel(catalog, show_tree=True)

        root_idx = model.index(0, 0)
        assert model.data(root_idx, DeviceSelectionModel.DottedPathRole) == "motor1"

        child_idx = model.index(0, 0, root_idx)
        child_path = model.data(child_idx, DeviceSelectionModel.DottedPathRole)
        assert "." in child_path
        assert child_path.startswith("motor1.")

    def test_check_state_independent(self, qapp):
        """Checking a child does not check parent."""
        devices = [_make_device_info("motor1", DeviceCategory.MOTOR)]
        catalog = _make_catalog(devices)
        model = DeviceSelectionModel(catalog, show_tree=True)

        root_idx = model.index(0, 0)
        child_idx = model.index(0, 0, root_idx)

        # Check the child
        model.setData(child_idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        assert model.data(child_idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        assert model.data(root_idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked

    def test_get_checked_paths(self, qapp):
        """get_checked_paths returns dotted paths of checked items."""
        devices = [_make_device_info("motor1", DeviceCategory.MOTOR)]
        catalog = _make_catalog(devices)
        model = DeviceSelectionModel(catalog, show_tree=True)

        root_idx = model.index(0, 0)
        child_idx = model.index(0, 0, root_idx)

        model.setData(root_idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        model.setData(child_idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)

        paths = model.get_checked_paths()
        assert "motor1" in paths
        child_path = model.data(child_idx, DeviceSelectionModel.DottedPathRole)
        assert child_path in paths

    def test_set_checked_paths(self, qapp):
        """set_checked_paths checks items by dotted path."""
        devices = [_make_device_info("motor1", DeviceCategory.MOTOR)]
        catalog = _make_catalog(devices)
        model = DeviceSelectionModel(catalog, show_tree=True)

        model.set_checked_paths(["motor1"])
        root_idx = model.index(0, 0)
        assert model.data(root_idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

    def test_writable_detection(self, qapp):
        """Writable signals are detected from ophyd metadata."""
        devices = [_make_device_info("motor1", DeviceCategory.MOTOR)]
        catalog = _make_catalog(devices)
        model = DeviceSelectionModel(catalog, show_tree=True)

        root_idx = model.index(0, 0)
        # Find readback (not writable) and setpoint (writable) children
        writable_map = {}
        for row in range(model.rowCount(root_idx)):
            child_idx = model.index(row, 0, root_idx)
            name = model.data(child_idx, Qt.ItemDataRole.DisplayRole)
            item = child_idx.internalPointer()
            writable_map[name] = item.is_writable

        assert writable_map.get("readback") is False
        assert writable_map.get("setpoint") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_selection_model.py::TestDeviceSelectionModel -v`
Expected: FAIL — `DeviceSelectionModel` not defined

- [ ] **Step 3: Implement DeviceSelectionModel**

Append to `src/lightfall/ui/models/device_selection.py`:

```python
from loguru import logger
from PySide6.QtCore import QAbstractItemModel, QModelIndex


class DeviceSelectionModel(QAbstractItemModel):
    """Checkable tree model of devices and their components.

    Builds a tree from DeviceCatalog for use in the device selector dialog.
    Each item is independently checkable. The checked items form the return
    value of the dialog.

    Args:
        catalog: DeviceCatalog to populate from.
        show_tree: If True, populate component children. If False, flat list.
        parent: Qt parent.
    """

    # Custom role for dotted path access
    DottedPathRole = Qt.ItemDataRole.UserRole + 1
    MetadataDictRole = Qt.ItemDataRole.UserRole + 2

    def __init__(
        self,
        catalog: Any,
        show_tree: bool = False,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._root = DeviceSelectionItem.create_root()
        self._show_tree = show_tree
        self._path_to_item: dict[str, DeviceSelectionItem] = {}
        self._populate(catalog)

    def _populate(self, catalog: Any) -> None:
        """Build the tree from catalog devices."""
        devices = catalog.get_all_devices()
        for device_info in sorted(devices, key=lambda d: d.name):
            ophyd_obj = device_info.ophyd_device
            is_writable = self._check_writable(ophyd_obj) if ophyd_obj else False

            kind_str = None
            if ophyd_obj is not None:
                try:
                    kind_str = ophyd_obj.kind.name
                except Exception:
                    pass

            item = DeviceSelectionItem(
                name=device_info.name,
                dotted_path=device_info.name,
                parent=self._root,
                category=device_info.category,
                is_writable=is_writable,
                kind=kind_str,
                device_info=device_info,
                ophyd_obj=ophyd_obj,
                node_type="device",
            )
            self._root.append_child(item)
            self._path_to_item[device_info.name] = item

            if self._show_tree and ophyd_obj is not None:
                self._add_components(item, ophyd_obj)

    def _add_components(
        self, parent_item: DeviceSelectionItem, ophyd_obj: Any
    ) -> None:
        """Recursively add ophyd components as children."""
        if not hasattr(ophyd_obj, "component_names"):
            return

        sig_attrs = getattr(ophyd_obj, "_sig_attrs", {})

        for comp_name in ophyd_obj.component_names:
            dotted = f"{parent_item.dotted_path}.{comp_name}"

            # Get the component object if already instantiated
            comp = getattr(ophyd_obj, "_signals", {}).get(comp_name)

            is_writable = False
            kind_str = None
            node_type = "signal"

            if comp is not None:
                is_writable = self._check_writable(comp)
                try:
                    kind_str = comp.kind.name
                except Exception:
                    pass

                # Check if sub-device
                try:
                    from ophyd import Device
                    if isinstance(comp, Device):
                        node_type = "device"
                except ImportError:
                    if hasattr(comp, "component_names"):
                        node_type = "device"
            else:
                # Not instantiated — determine type from sig_attrs
                cpt = sig_attrs.get(comp_name)
                if cpt is not None:
                    try:
                        from ophyd import Device
                        if issubclass(cpt.cls, Device):
                            node_type = "device"
                    except (TypeError, ImportError):
                        pass

            child = DeviceSelectionItem(
                name=comp_name,
                dotted_path=dotted,
                parent=parent_item,
                category=parent_item.category,
                is_writable=is_writable,
                kind=kind_str,
                ophyd_obj=comp,
                node_type=node_type,
            )
            parent_item.append_child(child)
            self._path_to_item[dotted] = child

            # Recurse into sub-devices
            if node_type == "device" and comp is not None:
                self._add_components(child, comp)

    @staticmethod
    def _check_writable(obj: Any) -> bool:
        """Check if an ophyd object is put-capable."""
        if obj is None:
            return False

        # Check _metadata dict (most reliable for signals)
        metadata = getattr(obj, "_metadata", None)
        if isinstance(metadata, dict):
            return metadata.get("write_access", True)

        # Fallback: check for read-only class pattern
        cls_name = type(obj).__name__
        if cls_name.endswith("RO"):
            return False

        return hasattr(obj, "put")

    # --- QAbstractItemModel interface ---

    def index(
        self, row: int, column: int, parent: QModelIndex = QModelIndex()
    ) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_item = parent.internalPointer() if parent.isValid() else self._root
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

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        item = parent.internalPointer() if parent.isValid() else self._root
        return item.child_count()

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        item: DeviceSelectionItem = index.internalPointer()

        if role == Qt.ItemDataRole.DisplayRole:
            return item.name
        if role == Qt.ItemDataRole.CheckStateRole:
            return item.check_state
        if role == Qt.ItemDataRole.ToolTipRole:
            parts = [item.dotted_path]
            if item.device_info:
                if item.device_info.description:
                    parts.append(item.device_info.description)
                parts.append(f"Category: {item.category.value if item.category else '?'}")
                parts.append(f"Class: {item.device_info.device_class}")
            if item.kind:
                parts.append(f"Kind: {item.kind}")
            parts.append(f"Writable: {item.is_writable}")
            return "\n".join(parts)
        if role == self.DottedPathRole:
            return item.dotted_path
        if role == self.MetadataDictRole:
            return item.metadata_dict()
        return None

    def setData(
        self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if not index.isValid():
            return False
        if role == Qt.ItemDataRole.CheckStateRole:
            item: DeviceSelectionItem = index.internalPointer()
            item.check_state = Qt.CheckState(value)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )

    # --- Public helpers ---

    def get_checked_paths(self) -> list[str]:
        """Return dotted paths of all checked items."""
        paths = []
        self._collect_checked(self._root, paths)
        return paths

    def _collect_checked(
        self, item: DeviceSelectionItem, paths: list[str]
    ) -> None:
        for child in item._children:
            if child.check_state == Qt.CheckState.Checked:
                paths.append(child.dotted_path)
            self._collect_checked(child, paths)

    def set_checked_paths(self, paths: list[str]) -> None:
        """Check items by dotted path."""
        path_set = set(paths)
        self._set_checked_recursive(self._root, path_set)

    def _set_checked_recursive(
        self, item: DeviceSelectionItem, path_set: set[str]
    ) -> None:
        for i, child in enumerate(item._children):
            new_state = (
                Qt.CheckState.Checked
                if child.dotted_path in path_set
                else Qt.CheckState.Unchecked
            )
            if child.check_state != new_state:
                child.check_state = new_state
                idx = self.createIndex(i, 0, child)
                self.dataChanged.emit(
                    idx, idx, [Qt.ItemDataRole.CheckStateRole]
                )
            self._set_checked_recursive(child, path_set)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_selection_model.py -v`
Expected: PASS (all TestDeviceSelectionItem + TestDeviceSelectionModel)

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lightfall/ui/models/device_selection.py tests/test_device_selection_model.py
git commit -m "feat: add DeviceSelectionModel with tree population and check state"
```

---

## Task 4: Create `DeviceSelectionFilterProxy`

**Files:**
- Modify: `src/lightfall/ui/models/device_selection.py`
- Modify: `tests/test_device_selection_model.py`

- [ ] **Step 1: Write tests for the filter proxy**

Append to `tests/test_device_selection_model.py`:

```python
from lightfall.ui.models.device_selection import DeviceSelectionFilterProxy


class TestDeviceSelectionFilterProxy:
    """Tests for the filter proxy model."""

    @pytest.fixture
    def motor_and_detector(self, qapp):
        """Model with a motor (has children) and a detector."""
        devices = [
            _make_device_info("motor1", DeviceCategory.MOTOR),
            _make_device_info("det1", DeviceCategory.DETECTOR),
        ]
        catalog = _make_catalog(devices)
        model = DeviceSelectionModel(catalog, show_tree=True)
        proxy = DeviceSelectionFilterProxy()
        proxy.setSourceModel(model)
        return model, proxy

    def test_no_filter_shows_all(self, motor_and_detector):
        """No filters set shows all top-level items."""
        model, proxy = motor_and_detector
        assert proxy.rowCount() == 2

    def test_category_filter(self, motor_and_detector):
        """Category filter shows only matching devices."""
        model, proxy = motor_and_detector
        proxy.set_categories({DeviceCategory.MOTOR})
        assert proxy.rowCount() == 1
        idx = proxy.index(0, 0)
        assert proxy.data(idx, Qt.ItemDataRole.DisplayRole) == "motor1"

    def test_category_filter_none_shows_all(self, motor_and_detector):
        """Setting categories=None clears the filter."""
        model, proxy = motor_and_detector
        proxy.set_categories({DeviceCategory.MOTOR})
        assert proxy.rowCount() == 1
        proxy.set_categories(None)
        assert proxy.rowCount() == 2

    def test_writable_only(self, motor_and_detector):
        """writable_only hides non-writable children."""
        model, proxy = motor_and_detector
        proxy.set_categories({DeviceCategory.MOTOR})
        proxy.set_writable_only(True)

        # Motor top-level should still be visible (it has writable children)
        assert proxy.rowCount() == 1
        motor_idx = proxy.index(0, 0)

        # Check that non-writable children (readback) are hidden
        visible_names = []
        for row in range(proxy.rowCount(motor_idx)):
            child_idx = proxy.index(row, 0, motor_idx)
            visible_names.append(proxy.data(child_idx, Qt.ItemDataRole.DisplayRole))
        assert "readback" not in visible_names
        assert "setpoint" in visible_names

    def test_kind_filter(self, motor_and_detector):
        """Kind filter shows only matching kinds."""
        model, proxy = motor_and_detector
        proxy.set_categories({DeviceCategory.MOTOR})
        # SynAxis.setpoint has kind=hinted, readback has kind=hinted,
        # velocity/acceleration have kind=config, unused has kind=omitted
        proxy.set_kinds({"config"})

        motor_idx = proxy.index(0, 0)
        visible_names = []
        for row in range(proxy.rowCount(motor_idx)):
            child_idx = proxy.index(row, 0, motor_idx)
            visible_names.append(proxy.data(child_idx, Qt.ItemDataRole.DisplayRole))
        assert "velocity" in visible_names
        assert "unused" not in visible_names

    def test_search_text(self, motor_and_detector):
        """Search text filters by name substring."""
        model, proxy = motor_and_detector
        proxy.set_search_text("det")
        assert proxy.rowCount() == 1
        idx = proxy.index(0, 0)
        assert proxy.data(idx, Qt.ItemDataRole.DisplayRole) == "det1"

    def test_custom_filter_func(self, motor_and_detector):
        """Custom filter function is applied."""
        model, proxy = motor_and_detector
        proxy.set_filter_func(lambda meta: meta["name"].startswith("motor"))
        assert proxy.rowCount() == 1

    def test_custom_sort_key(self, motor_and_detector):
        """Custom sort key controls ordering."""
        model, proxy = motor_and_detector
        # Reverse alphabetical
        proxy.set_sort_key(lambda meta: meta["name"])
        proxy.sort(0, Qt.SortOrder.DescendingOrder)

        first = proxy.data(proxy.index(0, 0), Qt.ItemDataRole.DisplayRole)
        second = proxy.data(proxy.index(1, 0), Qt.ItemDataRole.DisplayRole)
        assert first == "motor1"
        assert second == "det1"

    def test_parent_visible_if_child_matches(self, motor_and_detector):
        """Parent is visible when a child passes the filter."""
        model, proxy = motor_and_detector
        # Search for "setpoint" — only exists as child of motor1
        proxy.set_search_text("setpoint")
        assert proxy.rowCount() == 1
        idx = proxy.index(0, 0)
        assert proxy.data(idx, Qt.ItemDataRole.DisplayRole) == "motor1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_selection_model.py::TestDeviceSelectionFilterProxy -v`
Expected: FAIL — `DeviceSelectionFilterProxy` not defined

- [ ] **Step 3: Implement DeviceSelectionFilterProxy**

Append to `src/lightfall/ui/models/device_selection.py`:

```python
from collections.abc import Callable

from PySide6.QtCore import QSortFilterProxyModel


class DeviceSelectionFilterProxy(QSortFilterProxyModel):
    """Filter/sort proxy for the device selection model.

    Filters are AND-combined. A parent row is accepted if it passes
    the filter directly OR if any descendant passes.

    Supports:
        - Category filter (set of DeviceCategory)
        - Writable-only (put-capable signals)
        - Kind filter (set of ophyd Kind names)
        - Custom filter function
        - Search text (substring match on name/path/description)
        - Custom sort key function
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._categories: set | None = None
        self._writable_only: bool = False
        self._kinds: set[str] | None = None
        self._filter_func: Callable[[dict], bool] | None = None
        self._search_text: str = ""
        self._sort_key: Callable[[dict], Any] | None = None
        self.setRecursiveFilteringEnabled(True)

    # --- Filter setters (each invalidates the filter) ---

    def set_categories(self, categories: set | None) -> None:
        self._categories = categories
        self.invalidateFilter()

    def set_writable_only(self, writable_only: bool) -> None:
        self._writable_only = writable_only
        self.invalidateFilter()

    def set_kinds(self, kinds: set[str] | None) -> None:
        self._kinds = kinds
        self.invalidateFilter()

    def set_filter_func(self, func: Callable[[dict], bool] | None) -> None:
        self._filter_func = func
        self.invalidateFilter()

    def set_search_text(self, text: str) -> None:
        self._search_text = text.lower()
        self.invalidateFilter()

    def set_sort_key(self, key: Callable[[dict], Any] | None) -> None:
        self._sort_key = key
        self.invalidate()

    # --- QSortFilterProxyModel overrides ---

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex
    ) -> bool:
        idx = self.sourceModel().index(source_row, 0, source_parent)
        if not idx.isValid():
            return False
        item: DeviceSelectionItem = idx.internalPointer()
        return self._item_passes(item)

    def _item_passes(self, item: DeviceSelectionItem) -> bool:
        """Check if an item passes all active filters."""
        if self._categories is not None:
            if item.category not in self._categories:
                return False

        if self._writable_only and not item.is_writable:
            return False

        if self._kinds is not None:
            if item.kind not in self._kinds:
                return False

        if self._search_text:
            haystack = item.dotted_path.lower()
            desc = ""
            if item.device_info and item.device_info.description:
                desc = item.device_info.description.lower()
            if self._search_text not in haystack and self._search_text not in desc:
                return False

        if self._filter_func is not None:
            if not self._filter_func(item.metadata_dict()):
                return False

        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        if self._sort_key is not None:
            source = self.sourceModel()
            left_meta = source.data(left, DeviceSelectionModel.MetadataDictRole)
            right_meta = source.data(right, DeviceSelectionModel.MetadataDictRole)
            if left_meta is not None and right_meta is not None:
                return self._sort_key(left_meta) < self._sort_key(right_meta)
        # Default: alphabetical by display name
        left_name = self.sourceModel().data(left, Qt.ItemDataRole.DisplayRole) or ""
        right_name = self.sourceModel().data(right, Qt.ItemDataRole.DisplayRole) or ""
        return left_name < right_name
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_selection_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lightfall/ui/models/device_selection.py tests/test_device_selection_model.py
git commit -m "feat: add DeviceSelectionFilterProxy with category/writable/kind/text/func filters"
```

---

## Task 5: Rewrite `DeviceSelectorDialog`

**Files:**
- Rewrite: `src/lightfall/ui/widgets/device_selector.py`
- Create: `tests/test_device_selector_dialog.py`

- [ ] **Step 1: Write tests for the new dialog**

Create `tests/test_device_selector_dialog.py`:

```python
"""Tests for the new DeviceSelectorDialog."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt

from lightfall.devices.model import DeviceCategory, DeviceInfo


def _make_device_info(name: str, category: DeviceCategory = DeviceCategory.MOTOR) -> DeviceInfo:
    """Helper to create a DeviceInfo with a mock ophyd device."""
    from ophyd.sim import SynAxis, SynSignal

    if category == DeviceCategory.MOTOR:
        ophyd_dev = SynAxis(name=name)
    else:
        ophyd_dev = SynSignal(name=name, func=lambda: 1.0)

    info = DeviceInfo(name=name, category=category)
    info._ophyd_device = ophyd_dev
    return info


def _make_catalog(devices: list[DeviceInfo]) -> MagicMock:
    """Create a mock DeviceCatalog."""
    catalog = MagicMock()
    catalog.get_all_devices.return_value = devices
    return catalog


class TestDeviceSelectorDialog:
    """Tests for the QTreeView-based dialog."""

    @pytest.fixture
    def catalog(self):
        return _make_catalog([
            _make_device_info("motor1", DeviceCategory.MOTOR),
            _make_device_info("motor2", DeviceCategory.MOTOR),
            _make_device_info("det1", DeviceCategory.DETECTOR),
        ])

    def test_flat_mode_shows_devices(self, qapp, catalog):
        """Flat mode shows top-level devices only."""
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog

        dlg = DeviceSelectorDialog(catalog, show_tree=False)
        # The proxy model should have 3 rows
        assert dlg._proxy.rowCount() == 3

    def test_tree_mode_shows_children(self, qapp, catalog):
        """Tree mode shows device components."""
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog

        dlg = DeviceSelectorDialog(catalog, show_tree=True)
        # motor1 should have children
        motor_idx = dlg._proxy.index(0, 0)
        assert dlg._proxy.rowCount(motor_idx) >= 2

    def test_category_filter(self, qapp, catalog):
        """Category filter restricts visible devices."""
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog

        dlg = DeviceSelectorDialog(catalog, categories={DeviceCategory.DETECTOR})
        assert dlg._proxy.rowCount() == 1

    def test_initial_selection(self, qapp, catalog):
        """initial_selection pre-checks items."""
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog

        dlg = DeviceSelectorDialog(catalog, initial_selection=["motor1", "det1"])
        paths = dlg.get_selected_paths()
        assert "motor1" in paths
        assert "det1" in paths
        assert "motor2" not in paths

    def test_get_selected_paths_empty(self, qapp, catalog):
        """No selection returns empty list."""
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog

        dlg = DeviceSelectorDialog(catalog)
        assert dlg.get_selected_paths() == []

    def test_search_filters_view(self, qapp, catalog):
        """Typing in search box filters the tree."""
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog

        dlg = DeviceSelectorDialog(catalog)
        dlg._search_edit.setText("det")
        assert dlg._proxy.rowCount() == 1


class TestIconResolution:
    """Tests for the icon resolution helper."""

    def test_explicit_icon(self, qapp):
        """Explicit icon name is returned as-is (with prefix normalization)."""
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name

        assert resolve_button_icon_name(icon="mdi6.engine", categories=None) == "mdi6.engine"

    def test_explicit_icon_no_prefix(self, qapp):
        """Icon name without prefix gets mdi6. prepended."""
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name

        assert resolve_button_icon_name(icon="engine", categories=None) == "mdi6.engine"

    def test_auto_from_motor_category(self, qapp):
        """Motor category auto-resolves to mdi6.engine."""
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name

        result = resolve_button_icon_name(icon=None, categories={DeviceCategory.MOTOR})
        assert result == "mdi6.engine"

    def test_auto_from_detector_category(self, qapp):
        """Detector category auto-resolves to mdi6.camera."""
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name

        result = resolve_button_icon_name(icon=None, categories={DeviceCategory.DETECTOR})
        assert result == "mdi6.camera"

    def test_auto_from_controller_category(self, qapp):
        """Controller category auto-resolves to mdi6.tune-variant."""
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name

        result = resolve_button_icon_name(icon=None, categories={DeviceCategory.CONTROLLER})
        assert result == "mdi6.tune-variant"

    def test_multi_category_uses_default(self, qapp):
        """Multiple categories fall back to default icon."""
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name

        result = resolve_button_icon_name(
            icon=None, categories={DeviceCategory.MOTOR, DeviceCategory.DETECTOR}
        )
        assert result == "mdi6.microwave"

    def test_no_icon_no_category(self, qapp):
        """No icon and no category gives default."""
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name

        assert resolve_button_icon_name(icon=None, categories=None) == "mdi6.microwave"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_selector_dialog.py -v`
Expected: FAIL — old dialog doesn't have new constructor params

- [ ] **Step 3: Rewrite device_selector.py**

Replace the entire contents of `src/lightfall/ui/widgets/device_selector.py` with:

```python
"""Device selector for plan parameters.

Provides a QTreeView-based dialog for selecting devices and their
components from the DeviceCatalog, plus a custom pyqtgraph ParameterTree
type for device selection in plan configuration.

Usage:
    In parameter specs, use type='device':
        {'name': 'detectors', 'type': 'device', 'multi_select': True,
         'catalog': catalog, 'show_tree': True}
        {'name': 'motor', 'type': 'device', 'multi_select': False,
         'catalog': catalog, 'categories': {DeviceCategory.MOTOR}}
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

try:
    from pyqtgraph.parametertree import Parameter
    from pyqtgraph.parametertree.Parameter import PARAM_TYPES
    from pyqtgraph.parametertree.parameterTypes import (
        StrParameterItem,
        registerParameterType,
    )

    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False
    Parameter = None
    StrParameterItem = object
    registerParameterType = None
    PARAM_TYPES = {}

if TYPE_CHECKING:
    from lightfall.devices import DeviceCatalog, DeviceInfo
    from lightfall.devices.model import DeviceCategory


# Category -> QtAwesome icon name
_CATEGORY_ICON_MAP: dict[str, str] = {
    "motor": "mdi6.engine",
    "detector": "mdi6.camera",
    "controller": "mdi6.tune-variant",
}

_DEFAULT_ICON = "mdi6.microwave"


def resolve_button_icon_name(
    icon: str | None,
    categories: set[DeviceCategory] | None,
) -> str:
    """Resolve the QtAwesome icon name for a device parameter button.

    Resolution chain:
    1. Explicit icon (with mdi6. prefix normalization)
    2. Auto from single-category filter
    3. Default fallback

    Args:
        icon: Explicit icon name, or None.
        categories: Category filter set, or None.

    Returns:
        QtAwesome icon identifier string.
    """
    if icon is not None:
        return icon if "." in icon else f"mdi6.{icon}"

    if categories is not None and len(categories) == 1:
        cat = next(iter(categories))
        cat_value = cat.value if hasattr(cat, "value") else str(cat)
        return _CATEGORY_ICON_MAP.get(cat_value, _DEFAULT_ICON)

    return _DEFAULT_ICON


class DeviceSelectorDialog(QDialog):
    """Dialog for selecting devices and components from the catalog.

    Uses a QTreeView backed by DeviceSelectionModel and
    DeviceSelectionFilterProxy for filtering and sorting.

    Args:
        catalog: DeviceCatalog to select from.
        multi_select: Allow checking multiple items.
        show_tree: Show device component tree (children).
        categories: Only show devices of these categories.
        writable_only: Only show writable (put-capable) items.
        kinds: Only show items with these ophyd Kind names.
        filter_func: Custom filter function receiving metadata dict.
        sort_key: Custom sort key function receiving metadata dict.
        initial_selection: Dotted paths to pre-check.
        parent: Parent widget.
    """

    def __init__(
        self,
        catalog: DeviceCatalog,
        *,
        multi_select: bool = True,
        show_tree: bool = False,
        categories: set[DeviceCategory] | None = None,
        writable_only: bool = False,
        kinds: set[str] | None = None,
        filter_func: Callable[[dict], bool] | None = None,
        sort_key: Callable[[dict], Any] | None = None,
        initial_selection: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._multi_select = multi_select

        # Build models
        from lightfall.ui.models.device_selection import (
            DeviceSelectionFilterProxy,
            DeviceSelectionModel,
        )

        self._source_model = DeviceSelectionModel(
            catalog, show_tree=show_tree
        )
        self._proxy = DeviceSelectionFilterProxy()
        self._proxy.setSourceModel(self._source_model)

        # Apply filters
        if categories is not None:
            self._proxy.set_categories(categories)
        if writable_only:
            self._proxy.set_writable_only(True)
        if kinds is not None:
            self._proxy.set_kinds(kinds)
        if filter_func is not None:
            self._proxy.set_filter_func(filter_func)
        if sort_key is not None:
            self._proxy.set_sort_key(sort_key)

        self._setup_ui()

        # Apply initial selection
        if initial_selection:
            self._source_model.set_checked_paths(initial_selection)

        # Sort alphabetically by default
        self._proxy.sort(0)

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        self.setWindowTitle("Select Devices")
        self.setMinimumSize(400, 500)

        layout = QVBoxLayout(self)

        # Search box
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter devices...")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(self._search_edit)
        layout.addLayout(search_layout)

        # Tree view
        self._tree_view = QTreeView()
        self._tree_view.setModel(self._proxy)
        self._tree_view.setHeaderHidden(True)
        self._tree_view.setRootIsDecorated(
            self._source_model._show_tree
        )
        layout.addWidget(self._tree_view)

        # Selection info
        self._info_label = QLabel("0 items checked")
        layout.addWidget(self._info_label)

        # Connect model changes to update info label
        self._source_model.dataChanged.connect(self._on_data_changed)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        """Update the proxy filter when search text changes."""
        self._proxy.set_search_text(text)

    @Slot()
    def _on_data_changed(self) -> None:
        """Update the info label when check states change."""
        paths = self._source_model.get_checked_paths()
        count = len(paths)
        if count == 0:
            self._info_label.setText("0 items checked")
        elif count == 1:
            self._info_label.setText(f"1 item checked: {paths[0]}")
        else:
            self._info_label.setText(f"{count} items checked")

        # Enforce single-select: uncheck others if needed
        if not self._multi_select and count > 1:
            # Keep only the most recently checked
            # (the last one in the list since we just toggled it)
            self._source_model.set_checked_paths([paths[-1]])

    def get_selected_paths(self) -> list[str]:
        """Get the dotted paths of all checked items."""
        return self._source_model.get_checked_paths()

    def set_selected_paths(self, paths: list[str]) -> None:
        """Pre-check items by dotted path."""
        self._source_model.set_checked_paths(paths)


# --- pyqtgraph Parameter integration ---

if HAS_PYQTGRAPH:

    class DeviceParameterItem(StrParameterItem):
        """Parameter item for device selection with icon button.

        Shows a read-only text field with a QtAwesome icon button
        that opens the DeviceSelectorDialog.
        """

        def __init__(self, param, depth):
            self._value: list[str] = []
            super().__init__(param, depth)

            # Create button with icon
            self._select_button = QPushButton()
            self._select_button.setFixedWidth(28)
            self._select_button.setFixedHeight(22)
            self._select_button.setContentsMargins(0, 0, 0, 0)
            self._select_button.clicked.connect(self._open_device_dialog)
            self._apply_button_icon()
            self.layoutWidget.layout().insertWidget(2, self._select_button)

            self.displayLabel.resizeEvent = self._new_resize_event

        def _apply_button_icon(self) -> None:
            """Set the button icon from parameter options."""
            opts = self.param.opts
            icon_name = resolve_button_icon_name(
                icon=opts.get("icon"),
                categories=opts.get("categories"),
            )
            try:
                import qtawesome as qta

                self._select_button.setIcon(qta.icon(icon_name))
            except Exception:
                self._select_button.setText("...")

        def showEditor(self):
            super().showEditor()
            self._select_button.show()

        def hideEditor(self):
            super().hideEditor()
            self._select_button.show()

        def makeWidget(self):
            w = super().makeWidget()
            w.setValue = self.setValue
            w.value = self.value
            if hasattr(w, "sigChanging"):
                delattr(w, "sigChanging")
            return w

        def _new_resize_event(self, ev):
            ret = type(self.displayLabel).resizeEvent(self.displayLabel, ev)
            self.updateDisplayLabel()
            return ret

        def setValue(self, value):
            if isinstance(value, str):
                self._value = [n.strip() for n in value.split(",")] if value else []
            elif isinstance(value, list):
                self._value = list(value)
            else:
                self._value = []
            display_text = ", ".join(self._value) if self._value else ""
            self.widget.setText(display_text)

        def value(self):
            return self._value

        def _open_device_dialog(self):
            """Open the device selection dialog."""
            opts = self.param.opts
            catalog = opts.get("catalog")
            if catalog is None:
                logger.warning("No DeviceCatalog set for DeviceParameter")
                return

            dialog = DeviceSelectorDialog(
                catalog=catalog,
                multi_select=opts.get("multi_select", True),
                show_tree=opts.get("show_tree", False),
                categories=opts.get("categories"),
                writable_only=opts.get("writable_only", False),
                kinds=opts.get("kinds"),
                filter_func=opts.get("filter_func"),
                sort_key=opts.get("sort_key"),
                initial_selection=None,
                parent=None,
            )

            # Pre-select current values
            current = self.param.value() if self.param.hasValue() else []
            if current:
                dialog.set_selected_paths(current)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected = dialog.get_selected_paths()
                self.param.setValue(selected)

        def updateDefaultBtn(self):
            self.defaultBtn.setEnabled(
                not self.param.valueIsDefault() and self.param.opts["enabled"]
            )
            self.defaultBtn.setVisible(self.param.hasDefault())

        def updateDisplayLabel(self, value=None):
            lbl = self.displayLabel
            if value is None:
                value = self.param.value()
            if isinstance(value, list):
                value = ", ".join(value) if value else ""
            else:
                value = str(value) if value else ""
            font = lbl.font()
            metrics = QFontMetricsF(font)
            value = metrics.elidedText(
                value, Qt.TextElideMode.ElideRight, lbl.width() - 5
            )
            return super().updateDisplayLabel(value)

    class DeviceParameter(Parameter):
        """Parameter type for selecting devices from a DeviceCatalog.

        Options:
            catalog: DeviceCatalog instance.
            multi_select: Allow selecting multiple items (default: True).
            show_tree: Show component tree in dialog (default: False).
            categories: Set of DeviceCategory to filter by.
            writable_only: Only show writable items.
            kinds: Set of ophyd Kind names to filter by.
            filter_func: Custom filter function.
            sort_key: Custom sort key function.
            icon: QtAwesome icon name for the button.
        """

        itemClass = DeviceParameterItem

        def __init__(self, **opts):
            opts.setdefault("readonly", True)
            opts.setdefault("value", [])
            opts.setdefault("multi_select", True)
            super().__init__(**opts)

    if "device" not in PARAM_TYPES:
        registerParameterType("device", DeviceParameter)
        logger.debug("Registered 'device' parameter type")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_selector_dialog.py tests/test_device_selection_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lightfall/ui/widgets/device_selector.py tests/test_device_selector_dialog.py
git commit -m "feat: rewrite DeviceSelectorDialog with QTreeView and icon button"
```

---

## Task 6: Update `PlanConfigWidget` to Use New Dialog Parameters

**Files:**
- Modify: `src/lightfall/ui/widgets/plan_config.py`
- Modify: `tests/test_annotations.py` (update existing device param tests)

- [ ] **Step 1: Write tests for the updated _build_param_spec**

Update the existing device param tests in `tests/test_annotations.py`. Replace `TestPlanConfigAnnotations.test_device_param_with_filter`:

```python
    def test_device_param_with_filter(self, config_widget):
        """DeviceFilter annotation creates device parameter with categories."""
        from lightfall.devices.model import DeviceCategory

        def plan(
            motor: Annotated[Any, DeviceFilter(category="motor")],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        motor_param = root.child("motor")
        assert motor_param is not None
        assert motor_param.opts["type"] == "device"
        assert motor_param.opts["categories"] == {DeviceCategory.MOTOR}
        assert motor_param.opts["multi_select"] is False

    def test_device_param_with_multi_category(self, config_widget):
        """DeviceFilter with set category creates multi-category filter."""
        from lightfall.devices.model import DeviceCategory

        def plan(
            devices: Annotated[list, DeviceFilter(category={"motor", "controller"})],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        param = root.child("devices")
        assert param is not None
        assert param.opts["categories"] == {
            DeviceCategory.MOTOR, DeviceCategory.CONTROLLER
        }

    def test_device_param_with_icon(self, config_widget):
        """DeviceIcon annotation sets icon on parameter spec."""
        from lightfall.ui.annotations import DeviceIcon

        def plan(
            motor: Annotated[Any, DeviceFilter(category="motor"), DeviceIcon("mdi6.engine")],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        motor_param = root.child("motor")
        assert motor_param.opts.get("icon") == "mdi6.engine"

    def test_device_default_becomes_initial_selection(self, config_widget):
        """DeviceDefault translates to initial value on the parameter."""
        def plan(
            detectors: Annotated[list, DeviceFilter(category="detector"), DeviceDefault("det1")],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        param = root.child("detectors")
        assert param is not None
        assert param.value() == ["det1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_annotations.py::TestPlanConfigAnnotations::test_device_param_with_filter tests/test_annotations.py::TestPlanConfigAnnotations::test_device_param_with_multi_category tests/test_annotations.py::TestPlanConfigAnnotations::test_device_param_with_icon tests/test_annotations.py::TestPlanConfigAnnotations::test_device_default_becomes_initial_selection -v`
Expected: FAIL — old code sets `device_filter` not `categories`

- [ ] **Step 3: Update _build_param_spec in plan_config.py**

Replace the device parameter block in `_build_param_spec` (the `if category in (ParamCategory.DEVICE, ParamCategory.DEVICES):` branch, lines ~483-498) with:

```python
        if category in (ParamCategory.DEVICE, ParamCategory.DEVICES):
            from lightfall.devices.model import DeviceCategory as DC
            from lightfall.ui.annotations import DeviceDefault, DeviceIcon

            spec["type"] = "device"
            spec["value"] = []
            spec["catalog"] = self._catalog
            spec["multi_select"] = category == ParamCategory.DEVICES

            for meta in metadata:
                if isinstance(meta, DeviceFilter):
                    # Translate category to set[DeviceCategory]
                    if meta.category is not None:
                        if isinstance(meta.category, set):
                            spec["categories"] = {DC(c) for c in meta.category}
                        else:
                            spec["categories"] = {DC(meta.category)}
                    if meta.device_class is not None:
                        spec.setdefault("filter_func_parts", []).append(
                            lambda m, dc=meta.device_class: (
                                m["device_info"] is not None
                                and (
                                    m["device_info"].device_class == dc
                                    or m["device_info"].device_class.rsplit(".", 1)[-1] == dc
                                )
                            )
                        )
                    if meta.group is not None:
                        spec.setdefault("filter_func_parts", []).append(
                            lambda m, g=meta.group: (
                                m["device_info"] is not None
                                and g in m["device_info"].tags
                            )
                        )
                    if meta.name_pattern is not None:
                        import re as _re
                        spec.setdefault("filter_func_parts", []).append(
                            lambda m, p=meta.name_pattern: bool(
                                _re.match(p, m["name"], _re.IGNORECASE)
                            )
                        )
                elif isinstance(meta, DeviceFilterAny):
                    # Collect all categories from sub-filters
                    cats: set[DC] = set()
                    for flt in meta.filters:
                        if flt.category is not None:
                            if isinstance(flt.category, set):
                                cats.update(DC(c) for c in flt.category)
                            else:
                                cats.add(DC(flt.category))
                    if cats:
                        spec["categories"] = cats
                elif isinstance(meta, DeviceDefault):
                    if meta.names:
                        spec["value"] = list(meta.names)
                elif isinstance(meta, DeviceIcon):
                    spec["icon"] = meta.name

            # Combine filter_func_parts into a single filter_func
            parts = spec.pop("filter_func_parts", [])
            if parts:
                spec["filter_func"] = lambda m, _parts=parts: all(f(m) for f in _parts)
```

Also add `DeviceIcon` to the import block at the top of `_build_param_spec` (it already imports from `lightfall.ui.annotations`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_annotations.py::TestPlanConfigAnnotations -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_annotations.py tests/test_device_selection_model.py tests/test_device_selector_dialog.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lightfall/ui/widgets/plan_config.py tests/test_annotations.py
git commit -m "feat: update PlanConfigWidget to use new DeviceSelectorDialog parameters"
```

---

## Task 7: Clean Up Stale References

**Files:**
- Modify: `src/lightfall/ui/widgets/device_selector.py` (already rewritten — verify no stale imports)
- Modify: `tests/test_annotations.py` (remove old `test_device_param_with_filter_any` if superseded)

- [ ] **Step 1: Search for stale references to old dialog API**

Run:

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
grep -rn "category_filter\|DEVICE_CATEGORY_ICONS\|create_device_icon\|device_default\|device_filter" src/lightfall/ --include="*.py" | grep -v __pycache__ | grep -v ".pyc"
```

Fix any remaining references to the old API. The old `category_filter` and `device_filter` kwargs should be replaced with `categories` and the new parameters. The `DEVICE_CATEGORY_ICONS` dict and `create_device_icon` function should be gone from `device_selector.py`.

- [ ] **Step 2: Run the full test suite**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/ -v --timeout=30`
Expected: PASS (no regressions)

- [ ] **Step 3: Commit if changes were needed**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add -u
git commit -m "chore: clean up stale references to old device selector API"
```
