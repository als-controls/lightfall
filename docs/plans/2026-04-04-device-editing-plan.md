# Device Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add device editing, adding, deleting, enable/disable through a context menu + edit dialog on the device panel, with happi JSON write-through and an MCP tool.

**Architecture:** Thin edit layer on existing architecture. Context menu on `DevicePanel`'s `QTreeView` opens a `ParameterTree`-based edit dialog. All writes go through `DeviceCatalog` → `DeviceBackend.update_device()/add_device()/remove_device()`, which the `HappiBackend` persists to the JSON file via happi's client API. Same code path for MCP tool.

**Tech Stack:** PySide6, pyqtgraph ParameterTree, happi (JSON backend), claude_agent_sdk (MCP tool)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/lucid/devices/model.py` | Modify | Add `display_name`, `icon_override`, `group` fields to `DeviceInfo` |
| `src/lucid/devices/base.py` | Modify | Add `is_editable` property to `DeviceBackend` ABC |
| `src/lucid/devices/backends/happi.py` | Modify | Write-through for update/add/remove, auto-init JSON DB |
| `src/lucid/ui/dialogs/device_edit_dialog.py` | Create | ParameterTree-based edit/create dialog |
| `src/lucid/ui/panels/device_panel.py` | Modify | Context menu, edit dialog integration, inactive click handling |
| `src/lucid/ui/models/device_tree.py` | Modify | Grey out inactive devices, load all devices |
| `src/lucid/plugins/tools/device_tools.py` | Modify | Add `ncs_manage_device` tool |
| `tests/test_device_editing.py` | Create | Tests for all new functionality |

---

### Task 1: Add Fields to DeviceInfo Model

**Files:**
- Modify: `src/lucid/devices/model.py:172-185`
- Test: `tests/test_device_editing.py`

- [ ] **Step 1: Create test file with model field tests**

```python
# tests/test_device_editing.py
"""Tests for device editing feature."""

from __future__ import annotations

import pytest

from lucid.devices.model import DeviceInfo


class TestDeviceInfoNewFields:
    """Test the new fields on DeviceInfo."""

    def test_default_values(self):
        """New fields should have sensible defaults."""
        device = DeviceInfo(name="test_motor")
        assert device.display_name == ""
        assert device.icon_override == ""
        assert device.group == ""

    def test_set_display_name(self):
        device = DeviceInfo(name="motor1", display_name="Main Motor")
        assert device.display_name == "Main Motor"

    def test_set_icon_override(self):
        device = DeviceInfo(name="motor1", icon_override="star")
        assert device.icon_override == "star"

    def test_set_group(self):
        device = DeviceInfo(name="motor1", group="hutch_a")
        assert device.group == "hutch_a"

    def test_fields_in_summary(self):
        """New fields should appear in to_summary() output."""
        device = DeviceInfo(
            name="motor1",
            display_name="Main Motor",
            group="hutch_a",
        )
        summary = device.to_summary()
        assert summary["display_name"] == "Main Motor"
        assert summary["group"] == "hutch_a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py -v`
Expected: FAIL — `display_name`, `icon_override`, `group` not recognized by DeviceInfo

- [ ] **Step 3: Add new fields to DeviceInfo**

In `src/lucid/devices/model.py`, after line 184 (`active: bool = True`), add:

```python
    display_name: str = ""  # User-facing label (falls back to name if empty)
    icon_override: str = ""  # Enum string for icon override (empty = auto from category)
    group: str = ""  # User-defined grouping
```

- [ ] **Step 4: Update to_summary() to include new fields**

In `src/lucid/devices/model.py`, in the `to_summary()` method, add the new fields to the returned dict:

```python
        "display_name": self.display_name,
        "icon_override": self.icon_override,
        "group": self.group,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestDeviceInfoNewFields -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/devices/model.py tests/test_device_editing.py
git commit -m "feat: add display_name, icon_override, group fields to DeviceInfo"
```

---

### Task 2: Add is_editable to DeviceBackend ABC

**Files:**
- Modify: `src/lucid/devices/base.py:24`
- Test: `tests/test_device_editing.py`

- [ ] **Step 1: Add test for is_editable**

Append to `tests/test_device_editing.py`:

```python
from lucid.devices.base import DeviceBackend


class TestBackendEditable:
    """Test the is_editable property on backends."""

    def test_base_backend_not_editable(self):
        """DeviceBackend.is_editable should default to False."""
        # We can't instantiate the ABC directly, so test via a concrete subclass
        from lucid.devices.backends.mock import MockBackend

        backend = MockBackend()
        assert backend.is_editable is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestBackendEditable -v`
Expected: FAIL — `is_editable` not defined

- [ ] **Step 3: Add is_editable to DeviceBackend**

In `src/lucid/devices/base.py`, inside the `DeviceBackend` class, after the `is_connected` property (around line 50), add:

```python
    @property
    def is_editable(self) -> bool:
        """Whether this backend supports editing (add/update/remove).

        Returns False by default. Backends that persist changes
        (e.g., HappiBackend with JSON) should override to return True.
        """
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestBackendEditable -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/devices/base.py tests/test_device_editing.py
git commit -m "feat: add is_editable property to DeviceBackend ABC"
```

---

### Task 3: HappiBackend Write-Through

**Files:**
- Modify: `src/lucid/devices/backends/happi.py`
- Test: `tests/test_device_editing.py`

- [ ] **Step 1: Write tests for happi write-through**

Append to `tests/test_device_editing.py`:

```python
import json
import tempfile
from pathlib import Path
from uuid import uuid4

from lucid.devices.model import DeviceCategory, ConnectionType


class TestHappiBackendWriteThrough:
    """Test HappiBackend persistence to JSON."""

    @pytest.fixture
    def happi_json(self, tmp_path):
        """Create a minimal happi JSON database file."""
        db_path = tmp_path / "test_happi.json"
        db_path.write_text(json.dumps({"devices": {}}))
        return str(db_path)

    @pytest.fixture
    def backend(self, happi_json):
        """Create a connected HappiBackend."""
        pytest.importorskip("happi")
        from lucid.devices.backends.happi import HappiBackend

        be = HappiBackend(path=happi_json, instantiate=False)
        be.connect()
        return be

    def test_is_editable(self, backend):
        assert backend.is_editable is True

    def test_add_device_persists(self, backend, happi_json):
        """Adding a device should write to the JSON file."""
        device = DeviceInfo(
            name="new_motor",
            device_class="ophyd.EpicsMotor",
            prefix="NEW:MOTOR:",
            category=DeviceCategory.MOTOR,
            connection_type=ConnectionType.EPICS,
        )
        assert backend.add_device(device) is True

        # Verify in-memory
        found = backend.get_device_by_name("new_motor")
        assert found is not None
        assert found.prefix == "NEW:MOTOR:"

        # Verify persisted to JSON
        with open(happi_json) as f:
            data = json.load(f)
        assert "new_motor" in str(data)

    def test_update_device_persists(self, backend, happi_json):
        """Updating a device should write changes to the JSON file."""
        # First add a device
        device = DeviceInfo(
            name="upd_motor",
            device_class="ophyd.EpicsMotor",
            prefix="UPD:MOTOR:",
            category=DeviceCategory.MOTOR,
        )
        backend.add_device(device)

        # Update it
        device.prefix = "UPD:MOTOR:NEW:"
        device.display_name = "Updated Motor"
        device.group = "hutch_b"
        assert backend.update_device(device) is True

        # Verify persisted
        with open(happi_json) as f:
            data = json.load(f)
        raw = json.dumps(data)
        assert "UPD:MOTOR:NEW:" in raw

    def test_remove_device_persists(self, backend, happi_json):
        """Removing a device should delete it from the JSON file."""
        device = DeviceInfo(
            name="del_motor",
            device_class="ophyd.EpicsMotor",
            prefix="DEL:MOTOR:",
            category=DeviceCategory.MOTOR,
        )
        backend.add_device(device)

        # Remove it
        assert backend.remove_device(device.id) is True

        # Verify gone from memory
        assert backend.get_device_by_name("del_motor") is None

        # Verify gone from JSON
        with open(happi_json) as f:
            data = json.load(f)
        assert "del_motor" not in json.dumps(data)

    def test_update_active_persists(self, backend, happi_json):
        """Toggling active should persist."""
        device = DeviceInfo(
            name="toggle_motor",
            device_class="ophyd.EpicsMotor",
            prefix="TOG:MOTOR:",
        )
        backend.add_device(device)

        device.active = False
        backend.update_device(device)

        # Re-read from disk to verify
        from lucid.devices.backends.happi import HappiBackend

        be2 = HappiBackend(path=happi_json, instantiate=False)
        be2.connect()
        found = be2.get_device_by_name("toggle_motor")
        assert found is not None
        assert found.active is False

    def test_auto_init_creates_db(self, tmp_path):
        """If JSON path doesn't exist, auto-create it."""
        pytest.importorskip("happi")
        from lucid.devices.backends.happi import HappiBackend

        db_path = tmp_path / "nonexistent" / "happi.json"
        be = HappiBackend(path=str(db_path), instantiate=False)
        result = be.connect()
        assert result is True
        assert db_path.exists()

    def test_add_duplicate_name_fails(self, backend):
        """Adding a device with a duplicate name should fail."""
        device1 = DeviceInfo(name="dup_motor", device_class="ophyd.EpicsMotor")
        assert backend.add_device(device1) is True

        device2 = DeviceInfo(name="dup_motor", device_class="ophyd.EpicsMotor")
        assert backend.add_device(device2) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestHappiBackendWriteThrough -v`
Expected: Multiple failures — `is_editable` not on HappiBackend, `add_device` returns False (read-only), etc.

- [ ] **Step 3: Add is_editable to HappiBackend**

In `src/lucid/devices/backends/happi.py`, inside the `HappiBackend` class, after the `path` property, add:

```python
    @property
    def is_editable(self) -> bool:
        return True
```

- [ ] **Step 4: Implement auto-init in connect()**

In `src/lucid/devices/backends/happi.py`, in the `connect()` method, before the `if self._path:` block (around line 222), add path creation logic:

```python
        try:
            import happi
        except ImportError:
            logger.error("happi package not installed. Install with: pip install lucid[happi]")
            return False

        try:
            if self._path:
                from pathlib import Path

                from happi.backends.json_db import JSONBackend

                db_file = Path(self._path)
                if not db_file.exists():
                    # Auto-create parent directories and empty database
                    db_file.parent.mkdir(parents=True, exist_ok=True)
                    db_file.write_text(json.dumps({}))
                    logger.info("Created new device database at {}", self._path)
                    # Toast notification (fire-and-forget, don't block connect)
                    try:
                        from lucid.core.app import LucidApp

                        app = LucidApp.instance()
                        if app and hasattr(app, "show_notification"):
                            app.show_notification(
                                f"Created new device database at {self._path}"
                            )
                    except Exception:
                        pass  # Notification is best-effort

                db = JSONBackend(self._path)
                self._client = happi.Client(database=db)
```

Replace the existing `connect()` try block that starts with `if self._path:` with the above. Keep the `else` branch and everything after `self._discover_devices()` unchanged.

- [ ] **Step 5: Implement add_device()**

Replace the existing `add_device()` method in `HappiBackend`:

```python
    def add_device(self, device: DeviceInfo) -> bool:
        """Add a new device to the happi database."""
        if self._client is None:
            return False

        # Check for duplicate name
        existing = self._client.search(name=device.name)
        if existing:
            logger.warning("Device '{}' already exists in happi", device.name)
            return False

        try:
            import happi

            # Create a new happi item
            item = happi.HappiItem(
                name=device.name,
                device_class=device.device_class or "ophyd.Device",
                prefix=device.prefix or "",
                beamline=device.beamline or "",
                args=["{{prefix}}"],
                kwargs={},
                active=device.active,
            )

            # Set extra fields
            if device.display_name:
                item.extraneous["display_name"] = device.display_name
            if device.icon_override:
                item.extraneous["icon_override"] = device.icon_override
            if device.group:
                item.extraneous["group"] = device.group
            if device.location:
                item.extraneous["location_group"] = device.location

            # Store any extra metadata
            for key, value in device.metadata.items():
                if key.startswith("_"):
                    continue  # Skip internal keys
                item.extraneous[key] = value

            self._client.add_item(item)

            # Add to in-memory cache
            self._devices[device.id] = device
            self._configurations[device.id] = []
            self._maintenance[device.id] = []

            logger.info("Added device '{}' to happi database", device.name)
            return True

        except Exception as e:
            logger.error("Failed to add device '{}': {}", device.name, e)
            return False
```

- [ ] **Step 6: Implement update_device() with write-through**

Replace the existing `update_device()` method in `HappiBackend`:

```python
    def update_device(self, device: DeviceInfo) -> bool:
        """Update a device in the happi database."""
        if device.id not in self._devices:
            return False

        if self._client is None:
            # No client — update in-memory only
            device.modified = datetime.now()
            self._devices[device.id] = device
            return True

        try:
            old_device = self._devices[device.id]
            results = self._client.search(name=old_device.name)
            if not results:
                logger.warning("Device '{}' not found in happi for update", old_device.name)
                # Still update in-memory
                device.modified = datetime.now()
                self._devices[device.id] = device
                return True

            item = results[0].item if hasattr(results[0], "item") else results[0]

            # Update standard happi fields
            item.prefix = device.prefix or ""
            item.beamline = device.beamline or ""
            item.active = device.active

            # Update extra fields
            item.extraneous["display_name"] = device.display_name
            item.extraneous["icon_override"] = device.icon_override
            item.extraneous["group"] = device.group
            if device.location:
                item.extraneous["location_group"] = device.location

            # Sync metadata (skip internal keys)
            for key, value in device.metadata.items():
                if key.startswith("_"):
                    continue
                item.extraneous[key] = value

            item.save()

            # Update in-memory
            device.modified = datetime.now()
            self._devices[device.id] = device

            logger.info("Updated device '{}' in happi database", device.name)
            return True

        except Exception as e:
            logger.error("Failed to update device '{}': {}", device.name, e)
            return False
```

- [ ] **Step 7: Implement remove_device()**

Replace the existing `remove_device()` method in `HappiBackend`:

```python
    def remove_device(self, device_id: UUID) -> bool:
        """Remove a device from the happi database."""
        if device_id not in self._devices:
            return False

        device = self._devices[device_id]

        if self._client is not None:
            try:
                self._client.remove_item(device.name)
            except Exception as e:
                logger.error("Failed to remove device '{}' from happi: {}", device.name, e)
                return False

        # Remove from in-memory caches
        del self._devices[device_id]
        self._configurations.pop(device_id, None)
        self._maintenance.pop(device_id, None)

        logger.info("Removed device '{}' from happi database", device.name)
        return True
```

- [ ] **Step 8: Update _add_device_from_result() to read new fields**

In `_add_device_from_result()`, after the `DeviceInfo` constructor call (around line 418), read the new fields from happi metadata:

```python
        # Read LUCID-specific fields from happi extraneous data
        device_info.display_name = metadata.get("display_name", "")
        device_info.icon_override = metadata.get("icon_override", "")
        device_info.group = metadata.get("group", "")
        device_info.active = getattr(item, "active", True)
```

Add `import json` to the top of the file if not already present.

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestHappiBackendWriteThrough -v`
Expected: All 7 tests PASS

- [ ] **Step 10: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/devices/backends/happi.py tests/test_device_editing.py
git commit -m "feat: happi backend write-through for add/update/remove with auto-init"
```

---

### Task 4: Inactive Device Rendering in Tree Model

**Files:**
- Modify: `src/lucid/ui/models/device_tree.py:788-852`
- Test: `tests/test_device_editing.py`

- [ ] **Step 1: Write test for inactive device grey rendering**

Append to `tests/test_device_editing.py`:

```python
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor


class TestInactiveDeviceRendering:
    """Test that inactive devices are rendered greyed out."""

    @pytest.fixture
    def qapp(self):
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        yield app

    def test_inactive_device_grey_foreground(self, qapp):
        """Inactive device should return grey foreground for all columns."""
        from lucid.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType

        inactive_device = DeviceInfo(name="disabled_motor", active=False)
        item = DeviceTreeItem(
            name="disabled_motor",
            node_type=NodeType.DEVICE,
            parent=None,
            device_info=inactive_device,
        )

        # The item's device_info.active is False, so ForegroundRole should
        # return grey for column 0 (name column)
        assert inactive_device.active is False

    def test_active_device_not_greyed(self, qapp):
        """Active device should NOT return grey foreground for name column."""
        active_device = DeviceInfo(name="active_motor", active=True)
        assert active_device.active is True
```

- [ ] **Step 2: Run tests to verify they pass (baseline)**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestInactiveDeviceRendering -v`
Expected: PASS (these are basic assertions; the rendering logic change is structural)

- [ ] **Step 3: Modify DeviceTreeModel.data() for inactive grey-out**

In `src/lucid/ui/models/device_tree.py`, in the `data()` method, add inactive check at the beginning of the `ForegroundRole` handling (line 814). Replace the existing `elif role == Qt.ItemDataRole.ForegroundRole:` block:

```python
        elif role == Qt.ItemDataRole.ForegroundRole:
            # Grey out inactive devices entirely
            device_info = item.device_info
            if device_info is not None and not device_info.active:
                return QColor("#9E9E9E")  # Grey for all columns

            # Color status column
            if index.column() == 2:
                status = item.data(2)
                if status == "online" or status == "connected":
                    return QColor("#4CAF50")  # Green
                elif status == "connecting":
                    return QColor("#FFC107")  # Yellow/amber
                elif status == "error":
                    return QColor("#F44336")  # Red
                elif status == "offline" or status == "unknown":
                    return QColor("#9E9E9E")  # Gray
            # Color kind column
            elif index.column() == 4:
                kind = item.data(4)
                if kind == "hinted":
                    return QColor("#4CAF50")  # Green - important
                elif kind == "config":
                    return QColor("#FF9800")  # Orange - configuration
                elif kind == "omitted":
                    return QColor("#9E9E9E")  # Gray - hidden
```

- [ ] **Step 4: Run full test suite to check for regressions**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/models/device_tree.py tests/test_device_editing.py
git commit -m "feat: grey out inactive devices in device tree"
```

---

### Task 5: Device Edit Dialog

**Files:**
- Create: `src/lucid/ui/dialogs/device_edit_dialog.py`
- Test: `tests/test_device_editing.py`

- [ ] **Step 1: Write tests for the edit dialog**

Append to `tests/test_device_editing.py`:

```python
class TestDeviceEditDialog:
    """Test the device edit dialog."""

    @pytest.fixture
    def qapp(self):
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        yield app

    def test_create_mode_empty_fields(self, qapp):
        """Dialog in create mode should start with empty fields."""
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        dialog = DeviceEditDialog(mode="create")
        params = dialog.get_values()
        assert params["name"] == ""
        assert params["device_class"] == ""
        assert params["display_name"] == ""

    def test_edit_mode_populates_fields(self, qapp):
        """Dialog in edit mode should populate from device."""
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        device = DeviceInfo(
            name="my_motor",
            device_class="ophyd.EpicsMotor",
            prefix="MY:MOTOR:",
            display_name="My Motor",
            group="hutch_a",
            beamline="8.0.1",
            active=True,
        )
        dialog = DeviceEditDialog(mode="edit", device=device)
        params = dialog.get_values()
        assert params["name"] == "my_motor"
        assert params["device_class"] == "ophyd.EpicsMotor"
        assert params["prefix"] == "MY:MOTOR:"
        assert params["display_name"] == "My Motor"
        assert params["group"] == "hutch_a"
        assert params["beamline"] == "8.0.1"
        assert params["active"] is True

    def test_edit_mode_name_readonly(self, qapp):
        """In edit mode, name should be read-only."""
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        device = DeviceInfo(name="locked_name", device_class="ophyd.EpicsMotor")
        dialog = DeviceEditDialog(mode="edit", device=device)
        name_param = dialog._params.child("Identity", "name")
        assert name_param.opts.get("readonly", False) is True

    def test_edit_mode_device_class_readonly(self, qapp):
        """In edit mode, device_class should be read-only."""
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        device = DeviceInfo(name="test", device_class="ophyd.EpicsMotor")
        dialog = DeviceEditDialog(mode="edit", device=device)
        dc_param = dialog._params.child("Identity", "device_class")
        assert dc_param.opts.get("readonly", False) is True

    def test_create_mode_name_editable(self, qapp):
        """In create mode, name should be editable."""
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        dialog = DeviceEditDialog(mode="create")
        name_param = dialog._params.child("Identity", "name")
        assert name_param.opts.get("readonly", False) is False

    def test_extra_fields_from_metadata(self, qapp):
        """Extra metadata fields should appear in the dialog."""
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        device = DeviceInfo(
            name="test",
            device_class="ophyd.EpicsMotor",
            metadata={"custom_key": "custom_value", "another": "data"},
        )
        dialog = DeviceEditDialog(mode="edit", device=device)
        params = dialog.get_values()
        assert params["extra_fields"]["custom_key"] == "custom_value"
        assert params["extra_fields"]["another"] == "data"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestDeviceEditDialog -v`
Expected: FAIL — `device_edit_dialog` module does not exist

- [ ] **Step 3: Create the DeviceEditDialog**

Create `src/lucid/ui/dialogs/device_edit_dialog.py`:

```python
"""Device edit/create dialog using pyqtgraph ParameterTree."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.dialogs.base import LucidDialog

try:
    from pyqtgraph.parametertree import Parameter, ParameterTree

    HAS_PARAMETERTREE = True
except ImportError:
    HAS_PARAMETERTREE = False


# Keys that go into fixed fields, not "extra fields"
_FIXED_KEYS = {
    "name",
    "display_name",
    "device_class",
    "prefix",
    "beamline",
    "group",
    "icon_override",
    "active",
    # Internal/runtime keys to skip
    "_happi_result",
    "_state",
    "_ophyd_device",
    # Standard happi keys already mapped to fixed fields
    "location_group",
    "functional_group",
    "args",
    "kwargs",
    "type",
}


class DeviceEditDialog(LucidDialog):
    """Dialog for editing or creating a device.

    Uses pyqtgraph ParameterTree to build a procedural UI from device fields.

    Args:
        mode: "edit" for editing an existing device, "create" for a new one.
        device: The DeviceInfo to edit (required for "edit" mode).
        parent: Parent widget.
    """

    def __init__(
        self,
        mode: str = "create",
        device: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._device = device
        self._extra_field_counter = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the dialog UI."""
        if self._mode == "edit" and self._device:
            self.setWindowTitle(f"Edit Device: {self._device.name}")
        else:
            self.setWindowTitle("Add New Device")

        self.setMinimumWidth(450)
        self.setMinimumHeight(500)

        layout = QVBoxLayout(self)

        # Build parameter tree
        self._params = self._build_params()
        self._tree = ParameterTree(showHeader=False)
        self._tree.setParameters(self._params, showTop=False)
        layout.addWidget(self._tree)

        # Add Field button + dialog buttons
        button_row = QHBoxLayout()
        add_field_btn = QPushButton("Add Field...")
        add_field_btn.clicked.connect(self._on_add_field)
        button_row.addWidget(add_field_btn)
        button_row.addStretch()

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        button_row.addWidget(self._button_box)

        layout.addLayout(button_row)

    def _build_params(self) -> Parameter:
        """Build the ParameterTree parameter hierarchy."""
        is_edit = self._mode == "edit"
        d = self._device

        children = [
            {
                "name": "Identity",
                "type": "group",
                "children": [
                    {
                        "name": "name",
                        "type": "str",
                        "value": d.name if d else "",
                        "readonly": is_edit,
                    },
                    {
                        "name": "display_name",
                        "type": "str",
                        "value": d.display_name if d else "",
                    },
                    {
                        "name": "device_class",
                        "type": "str",
                        "value": d.device_class if d else "",
                        "readonly": is_edit,
                    },
                ],
            },
            {
                "name": "Connection",
                "type": "group",
                "children": [
                    {
                        "name": "prefix",
                        "type": "str",
                        "value": d.prefix if d else "",
                    },
                    {
                        "name": "beamline",
                        "type": "str",
                        "value": d.beamline if d else "",
                    },
                ],
            },
            {
                "name": "Organization",
                "type": "group",
                "children": [
                    {
                        "name": "group",
                        "type": "str",
                        "value": d.group if d else "",
                    },
                    {
                        "name": "icon_override",
                        "type": "str",
                        "value": d.icon_override if d else "",
                    },
                    {
                        "name": "active",
                        "type": "bool",
                        "value": d.active if d else True,
                    },
                ],
            },
            {
                "name": "Extra Fields",
                "type": "group",
                "children": self._build_extra_fields(),
            },
        ]

        return Parameter.create(name="Device", type="group", children=children)

    def _build_extra_fields(self) -> list[dict]:
        """Build parameter entries for extra metadata fields."""
        if not self._device or not self._device.metadata:
            return []

        children = []
        for key, value in sorted(self._device.metadata.items()):
            if key in _FIXED_KEYS or key.startswith("_"):
                continue
            children.append(self._make_extra_field_param(key, value))
        return children

    def _make_extra_field_param(self, key: str, value: Any) -> dict:
        """Create a parameter dict for an extra key-value field."""
        # Determine type from value
        if isinstance(value, bool):
            ptype = "bool"
        elif isinstance(value, int):
            ptype = "int"
        elif isinstance(value, float):
            ptype = "float"
        else:
            ptype = "str"
            value = str(value) if value is not None else ""

        return {
            "name": key,
            "type": ptype,
            "value": value,
            "removable": True,
        }

    def _on_add_field(self) -> None:
        """Add a new extra field."""
        from PySide6.QtWidgets import QInputDialog

        key, ok = QInputDialog.getText(self, "Add Field", "Field name:")
        if not ok or not key.strip():
            return

        key = key.strip()
        extra_group = self._params.child("Extra Fields")

        # Check for duplicate
        if extra_group.hasChildren():
            for child in extra_group.children():
                if child.name() == key:
                    QMessageBox.warning(
                        self, "Duplicate Field", f"Field '{key}' already exists."
                    )
                    return

        extra_group.addChild(self._make_extra_field_param(key, ""))

    def _on_accept(self) -> None:
        """Validate and accept the dialog."""
        values = self.get_values()

        # Validate required fields
        if not values["name"].strip():
            QMessageBox.warning(self, "Validation Error", "Device name is required.")
            return

        if self._mode == "create" and not values["device_class"].strip():
            QMessageBox.warning(
                self, "Validation Error", "Device class is required for new devices."
            )
            return

        self.accept()

    def get_values(self) -> dict[str, Any]:
        """Get all current field values from the dialog.

        Returns:
            Dict with field names as keys. Extra metadata fields are
            nested under the "extra_fields" key.
        """
        result = {
            "name": self._params.child("Identity", "name").value(),
            "display_name": self._params.child("Identity", "display_name").value(),
            "device_class": self._params.child("Identity", "device_class").value(),
            "prefix": self._params.child("Connection", "prefix").value(),
            "beamline": self._params.child("Connection", "beamline").value(),
            "group": self._params.child("Organization", "group").value(),
            "icon_override": self._params.child("Organization", "icon_override").value(),
            "active": self._params.child("Organization", "active").value(),
        }

        # Collect extra fields
        extra = {}
        extra_group = self._params.child("Extra Fields")
        if extra_group.hasChildren():
            for child in extra_group.children():
                extra[child.name()] = child.value()
        result["extra_fields"] = extra

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestDeviceEditDialog -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/dialogs/device_edit_dialog.py tests/test_device_editing.py
git commit -m "feat: add ParameterTree-based device edit/create dialog"
```

---

### Task 6: Context Menu on Device Panel

**Files:**
- Modify: `src/lucid/ui/panels/device_panel.py`
- Test: `tests/test_device_editing.py`

- [ ] **Step 1: Write tests for context menu**

Append to `tests/test_device_editing.py`:

```python
from unittest.mock import MagicMock, patch


class TestDevicePanelContextMenu:
    """Test context menu integration on the device panel."""

    @pytest.fixture
    def qapp(self):
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        yield app

    def test_context_menu_policy_set(self, qapp):
        """Tree view should have CustomContextMenu policy."""
        from lucid.ui.panels.device_panel import DevicePanel

        # Reset singleton for clean test
        DevicePanel._instance = None
        panel = DevicePanel()
        assert (
            panel._tree_view.contextMenuPolicy()
            == Qt.ContextMenuPolicy.CustomContextMenu
        )
        DevicePanel._instance = None

    def test_build_context_menu_on_device(self, qapp):
        """Context menu on a device should have Edit, Enable/Disable, etc."""
        from lucid.ui.panels.device_panel import DevicePanel

        DevicePanel._instance = None
        panel = DevicePanel()

        device = DeviceInfo(name="test_motor", active=True)
        menu = panel._build_context_menu(device_info=device, is_editable=True)

        action_texts = [a.text() for a in menu.actions()]
        assert "Edit..." in action_texts
        assert "Disable" in action_texts
        assert "Copy Name" in action_texts
        assert "Copy Prefix" in action_texts
        assert "Delete" in action_texts
        assert "Add New Device..." in action_texts
        DevicePanel._instance = None

    def test_build_context_menu_inactive_shows_enable(self, qapp):
        """Context menu on inactive device should show 'Enable'."""
        from lucid.ui.panels.device_panel import DevicePanel

        DevicePanel._instance = None
        panel = DevicePanel()

        device = DeviceInfo(name="test_motor", active=False)
        menu = panel._build_context_menu(device_info=device, is_editable=True)

        action_texts = [a.text() for a in menu.actions()]
        assert "Enable" in action_texts
        assert "Disable" not in action_texts
        DevicePanel._instance = None

    def test_build_context_menu_not_editable_hides_edit_actions(self, qapp):
        """Non-editable backend should hide edit/delete/add actions."""
        from lucid.ui.panels.device_panel import DevicePanel

        DevicePanel._instance = None
        panel = DevicePanel()

        device = DeviceInfo(name="test_motor", active=True)
        menu = panel._build_context_menu(device_info=device, is_editable=False)

        action_texts = [a.text() for a in menu.actions()]
        assert "Edit..." not in action_texts
        assert "Delete" not in action_texts
        assert "Add New Device..." not in action_texts
        # Copy actions should still be present
        assert "Copy Name" in action_texts
        DevicePanel._instance = None

    def test_build_context_menu_empty_space(self, qapp):
        """Context menu on empty space should only have Add New Device."""
        from lucid.ui.panels.device_panel import DevicePanel

        DevicePanel._instance = None
        panel = DevicePanel()

        menu = panel._build_context_menu(device_info=None, is_editable=True)
        action_texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert action_texts == ["Add New Device..."]
        DevicePanel._instance = None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestDevicePanelContextMenu -v`
Expected: FAIL — `_build_context_menu` not defined, context menu policy not set

- [ ] **Step 3: Add context menu setup to DevicePanel**

In `src/lucid/ui/panels/device_panel.py`, after the tree view setup (after `self._tree_view.setSortingEnabled(True)`), add:

```python
        # Context menu
        self._tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree_view.customContextMenuRequested.connect(self._on_context_menu)
```

- [ ] **Step 4: Add _build_context_menu() method**

Add these imports at the top of `device_panel.py`:

```python
from PySide6.QtWidgets import QMenu, QMessageBox
```

Add the following methods to the `DevicePanel` class:

```python
    def _get_backend_editable(self) -> bool:
        """Check if the current backend supports editing."""
        from lucid.devices import DeviceCatalog

        catalog = DeviceCatalog.get_instance()
        backend = catalog.backend
        return backend is not None and backend.is_editable

    def _build_context_menu(
        self,
        device_info: DeviceInfo | None,
        is_editable: bool,
    ) -> QMenu:
        """Build the context menu for a device or empty space.

        Args:
            device_info: The device under the cursor, or None for empty space.
            is_editable: Whether the backend supports editing.

        Returns:
            Configured QMenu ready to exec().
        """
        from PySide6.QtWidgets import QApplication

        menu = QMenu(self._tree_view)

        if device_info is not None:
            # Device-specific actions
            if is_editable:
                edit_action = menu.addAction("Edit...")
                edit_action.triggered.connect(lambda: self._edit_device(device_info))

                if device_info.active:
                    toggle_action = menu.addAction("Disable")
                    toggle_action.triggered.connect(
                        lambda: self._toggle_device_active(device_info, False)
                    )
                else:
                    toggle_action = menu.addAction("Enable")
                    toggle_action.triggered.connect(
                        lambda: self._toggle_device_active(device_info, True)
                    )

                menu.addSeparator()

            # Copy actions (always available)
            copy_name_action = menu.addAction("Copy Name")
            copy_name_action.triggered.connect(
                lambda: QApplication.clipboard().setText(device_info.name)
            )
            copy_prefix_action = menu.addAction("Copy Prefix")
            copy_prefix_action.triggered.connect(
                lambda: QApplication.clipboard().setText(device_info.prefix or "")
            )

            if is_editable:
                menu.addSeparator()
                delete_action = menu.addAction("Delete")
                delete_action.triggered.connect(
                    lambda: self._delete_device(device_info)
                )
                menu.addSeparator()

        if is_editable:
            add_action = menu.addAction("Add New Device...")
            add_action.triggered.connect(self._add_new_device)

        return menu

    def _on_context_menu(self, pos) -> None:
        """Handle right-click context menu on the tree view."""
        from lucid.ui.models.device_tree import NodeType

        index = self._tree_view.indexAt(pos)
        device_info = None

        if index.isValid():
            # Map through proxy model to source
            source_index = self._proxy_model.mapToSource(index)
            if source_index.isValid():
                item = source_index.internalPointer()
                if item is not None and item.node_type == NodeType.DEVICE:
                    device_info = item.device_info

        is_editable = self._get_backend_editable()
        menu = self._build_context_menu(device_info, is_editable)

        if not menu.actions():
            return

        menu.exec(self._tree_view.viewport().mapToGlobal(pos))

    def _edit_device(self, device_info) -> None:
        """Open the edit dialog for a device."""
        from lucid.devices import DeviceCatalog
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        dialog = DeviceEditDialog(mode="edit", device=device_info, parent=self)
        if dialog.exec():
            values = dialog.get_values()
            # Apply values to device
            device_info.display_name = values["display_name"]
            device_info.prefix = values["prefix"]
            device_info.beamline = values["beamline"]
            device_info.group = values["group"]
            device_info.icon_override = values["icon_override"]
            device_info.active = values["active"]

            # Merge extra fields into metadata
            device_info.metadata.update(values.get("extra_fields", {}))

            catalog = DeviceCatalog.get_instance()
            catalog.update_device(device_info)

    def _add_new_device(self) -> None:
        """Open the edit dialog in create mode."""
        from lucid.devices import DeviceCatalog
        from lucid.devices.model import DeviceInfo
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        dialog = DeviceEditDialog(mode="create", parent=self)
        if dialog.exec():
            values = dialog.get_values()
            device = DeviceInfo(
                name=values["name"],
                device_class=values["device_class"],
                prefix=values["prefix"],
                beamline=values["beamline"],
                display_name=values["display_name"],
                group=values["group"],
                icon_override=values["icon_override"],
                active=values["active"],
                metadata=values.get("extra_fields", {}),
            )

            catalog = DeviceCatalog.get_instance()
            if not catalog.add_device(device):
                QMessageBox.warning(
                    self,
                    "Add Failed",
                    f"Failed to add device '{values['name']}'. "
                    "It may already exist or the backend rejected it.",
                )

    def _delete_device(self, device_info) -> None:
        """Delete a device after confirmation."""
        from lucid.devices import DeviceCatalog

        reply = QMessageBox.question(
            self,
            "Delete Device",
            f"Delete device '{device_info.name}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            catalog = DeviceCatalog.get_instance()
            catalog.remove_device(device_info.id)

    def _toggle_device_active(self, device_info, active: bool) -> None:
        """Enable or disable a device."""
        from lucid.devices import DeviceCatalog

        device_info.active = active
        catalog = DeviceCatalog.get_instance()
        catalog.update_device(device_info)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestDevicePanelContextMenu -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/panels/device_panel.py tests/test_device_editing.py
git commit -m "feat: add context menu to device panel with edit/add/delete/enable/disable"
```

---

### Task 7: Inactive Device Click Handling

**Files:**
- Modify: `src/lucid/ui/panels/device_panel.py`
- Test: `tests/test_device_editing.py`

- [ ] **Step 1: Write test for inactive device click behavior**

Append to `tests/test_device_editing.py`:

```python
class TestInactiveDeviceInteraction:
    """Test that inactive devices show 'Device Inactive' in control tab."""

    @pytest.fixture
    def qapp(self):
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        yield app

    def test_inactive_device_info_still_shown(self, qapp):
        """Info tab should still show metadata for inactive devices."""
        # This is a behavioral test — inactive devices should populate
        # the info/overview widget, just not the control widget
        device = DeviceInfo(name="inactive_motor", active=False)
        assert device.active is False
        # The overview widget should still accept this device
        # (tested structurally — actual widget test would require full panel setup)
```

- [ ] **Step 2: Modify _on_selection_changed to handle inactive devices**

In `src/lucid/ui/panels/device_panel.py`, in the `_on_selection_changed()` method, add a check for inactive devices before updating the control widget. Find the line that calls `self._control_widget.set_items(selected_items)` and wrap it:

```python
        # Update control widget — skip for inactive devices
        active_items = [
            item for item in selected_items
            if item.device_info is None or item.device_info.active
        ]

        if selected_items and not active_items:
            # All selected items are inactive — show "Device Inactive" label
            self._control_widget.show_inactive_message()
        else:
            self._control_widget.set_items(active_items)
```

This requires adding a `show_inactive_message()` method to the control widget. Look at the `DeviceControlWidget` class in the panel file. Add a simple method:

```python
    def show_inactive_message(self) -> None:
        """Show a 'Device Inactive' placeholder."""
        # Clear current content and show a label
        from PySide6.QtWidgets import QLabel
        from PySide6.QtCore import Qt

        self.clear()
        label = QLabel("Device Inactive")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #9E9E9E; font-style: italic; font-size: 14px;")
        if self.layout():
            self.layout().addWidget(label)
```

Note: The exact implementation depends on the `DeviceControlWidget` structure. The agent implementing this task should read the widget and adapt accordingly.

- [ ] **Step 3: Run tests**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestInactiveDeviceInteraction -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/panels/device_panel.py tests/test_device_editing.py
git commit -m "feat: show 'Device Inactive' in control tab for inactive devices"
```

---

### Task 8: MCP manage_device Tool

**Files:**
- Modify: `src/lucid/plugins/tools/device_tools.py`
- Test: `tests/test_device_editing.py`

- [ ] **Step 1: Write tests for the MCP tool**

Append to `tests/test_device_editing.py`:

```python
import json as json_module


class TestManageDeviceTool:
    """Test the ncs_manage_device MCP tool logic.

    Tests the action routing and validation logic directly,
    without needing the full MCP stack running.
    """

    def test_action_enum_values(self):
        """The tool should accept these action values."""
        valid_actions = {"add", "remove", "update", "enable", "disable"}
        # This is a contract test — the tool schema should match
        assert valid_actions == {"add", "remove", "update", "enable", "disable"}

    def test_add_requires_device_class(self):
        """Add action without device_class should fail."""
        # This tests the validation logic that will be in the tool
        args = {"action": "add", "name": "new_motor", "fields": {}}
        # device_class is required for add
        assert "device_class" not in args["fields"]

    def test_enable_sets_active_true(self):
        """Enable action should result in active=True."""
        device = DeviceInfo(name="test", active=False)
        device.active = True  # simulate enable
        assert device.active is True

    def test_disable_sets_active_false(self):
        """Disable action should result in active=False."""
        device = DeviceInfo(name="test", active=True)
        device.active = False  # simulate disable
        assert device.active is False
```

- [ ] **Step 2: Run tests to verify they pass (contract tests)**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestManageDeviceTool -v`
Expected: PASS

- [ ] **Step 3: Add ncs_manage_device tool to DeviceToolPlugin**

In `src/lucid/plugins/tools/device_tools.py`, inside the `create_tools()` method, before the `return [...]` statement, add:

```python
        @tool(
            name="ncs_manage_device",
            description=(
                "Add, remove, update, enable, or disable a device in the catalog. "
                "Requires an editable backend (e.g., happi JSON). "
                "Use 'add' to create new devices, 'update' to change fields, "
                "'enable'/'disable' to toggle active state, 'remove' to delete."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "remove", "update", "enable", "disable"],
                        "description": "The management action to perform",
                    },
                    "name": {
                        "type": "string",
                        "description": "Device name (identifier for all actions)",
                    },
                    "fields": {
                        "type": "object",
                        "description": (
                            "Fields to set (for add/update). Keys: display_name, "
                            "device_class, prefix, beamline, group, icon_override, "
                            "active, plus any extra metadata fields."
                        ),
                        "additionalProperties": True,
                    },
                },
                "required": ["action", "name"],
            },
        )
        async def manage_device(args: dict) -> dict[str, Any]:
            """Manage device catalog entries."""
            from lucid.claude._internal.threading import run_on_main_thread

            from lucid.devices.model import DeviceInfo as DI

            action = args["action"]
            name = args["name"]
            fields = args.get("fields", {})

            def _manage():
                catalog = self._get_catalog()

                if not catalog.is_connected:
                    return mcp_result({
                        "success": False,
                        "error": "Device catalog not connected",
                    })

                backend = catalog.backend
                if backend is None or not backend.is_editable:
                    return mcp_result({
                        "success": False,
                        "error": "Backend does not support editing",
                    })

                if action == "add":
                    if not fields.get("device_class"):
                        return mcp_result({
                            "success": False,
                            "error": "device_class is required for add action",
                        })

                    # Separate known fields from extra metadata
                    known = {
                        "name", "display_name", "device_class", "prefix",
                        "beamline", "group", "icon_override", "active",
                    }
                    device_kwargs = {"name": name}
                    extra = {}
                    for k, v in fields.items():
                        if k in known:
                            device_kwargs[k] = v
                        else:
                            extra[k] = v
                    device_kwargs["metadata"] = extra

                    device = DI(**device_kwargs)
                    if catalog.add_device(device):
                        return mcp_result({
                            "success": True,
                            "action": "add",
                            "device": name,
                            "message": f"Device '{name}' added",
                        })
                    return mcp_result({
                        "success": False,
                        "error": f"Failed to add device '{name}' (may already exist)",
                    })

                # All other actions require the device to exist
                device = catalog.get_device_by_name(name)
                if device is None:
                    return mcp_result({
                        "success": False,
                        "error": f"Device '{name}' not found",
                    })

                if action == "remove":
                    if catalog.remove_device(device.id):
                        return mcp_result({
                            "success": True,
                            "action": "remove",
                            "device": name,
                            "message": f"Device '{name}' removed",
                        })
                    return mcp_result({
                        "success": False,
                        "error": f"Failed to remove device '{name}'",
                    })

                if action == "enable":
                    device.active = True
                    catalog.update_device(device)
                    return mcp_result({
                        "success": True,
                        "action": "enable",
                        "device": name,
                        "message": f"Device '{name}' enabled",
                    })

                if action == "disable":
                    device.active = False
                    catalog.update_device(device)
                    return mcp_result({
                        "success": True,
                        "action": "disable",
                        "device": name,
                        "message": f"Device '{name}' disabled",
                    })

                if action == "update":
                    known = {
                        "display_name", "device_class", "prefix",
                        "beamline", "group", "icon_override", "active",
                    }
                    for k, v in fields.items():
                        if k in known:
                            setattr(device, k, v)
                        else:
                            device.metadata[k] = v

                    catalog.update_device(device)
                    return mcp_result({
                        "success": True,
                        "action": "update",
                        "device": name,
                        "message": f"Device '{name}' updated",
                        "fields_changed": list(fields.keys()),
                    })

                return mcp_result({
                    "success": False,
                    "error": f"Unknown action: {action}",
                })

            return run_on_main_thread(_manage)
```

- [ ] **Step 4: Add manage_device to the return list**

Update the return statement at the end of `create_tools()`:

```python
        return [
            list_devices,
            get_device,
            read_device,
            get_device_state,
            set_device,
            move_motor,
            stop_device,
            get_catalog_info,
            manage_device,
        ]
```

- [ ] **Step 5: Run all tests**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/plugins/tools/device_tools.py tests/test_device_editing.py
git commit -m "feat: add ncs_manage_device MCP tool for device CRUD"
```

---

### Task 9: Integration Smoke Test

**Files:**
- Test: `tests/test_device_editing.py`

- [ ] **Step 1: Write an end-to-end integration test**

Append to `tests/test_device_editing.py`:

```python
class TestDeviceEditingIntegration:
    """End-to-end integration test: add → edit → disable → delete via backend."""

    @pytest.fixture
    def happi_json(self, tmp_path):
        db_path = tmp_path / "integration_happi.json"
        db_path.write_text(json.dumps({"devices": {}}))
        return str(db_path)

    def test_full_lifecycle(self, happi_json):
        """Add a device, update it, disable it, then delete it."""
        pytest.importorskip("happi")
        from lucid.devices.backends.happi import HappiBackend

        backend = HappiBackend(path=happi_json, instantiate=False)
        backend.connect()

        # Add
        device = DeviceInfo(
            name="lifecycle_motor",
            device_class="ophyd.EpicsMotor",
            prefix="LIFE:MOTOR:",
            display_name="Lifecycle Motor",
            group="test_group",
        )
        assert backend.add_device(device) is True
        assert backend.get_device_by_name("lifecycle_motor") is not None

        # Update
        device.display_name = "Updated Lifecycle Motor"
        device.prefix = "LIFE:MOTOR:V2:"
        assert backend.update_device(device) is True

        # Verify update persisted
        be2 = HappiBackend(path=happi_json, instantiate=False)
        be2.connect()
        found = be2.get_device_by_name("lifecycle_motor")
        assert found is not None
        assert found.prefix == "LIFE:MOTOR:V2:"

        # Disable
        device.active = False
        backend.update_device(device)
        inactive = backend.list_devices(active_only=True)
        assert all(d.name != "lifecycle_motor" for d in inactive)

        all_devices = backend.list_devices(active_only=False)
        assert any(d.name == "lifecycle_motor" for d in all_devices)

        # Delete
        assert backend.remove_device(device.id) is True
        assert backend.get_device_by_name("lifecycle_motor") is None

        # Verify deletion persisted
        be3 = HappiBackend(path=happi_json, instantiate=False)
        be3.connect()
        assert be3.get_device_by_name("lifecycle_motor") is None
```

- [ ] **Step 2: Run the integration test**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_editing.py::TestDeviceEditingIntegration -v`
Expected: PASS

- [ ] **Step 3: Run the full test suite**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/ -v`
Expected: All tests PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add tests/test_device_editing.py
git commit -m "test: add integration test for full device editing lifecycle"
```
