# Device Editing, Enable/Disable, and Management

**Date:** 2026-04-04
**Status:** Draft

## Overview

Add the ability to edit, add, delete, enable, and disable devices in LUCID through both a GUI context menu + edit dialog and an MCP tool. Changes persist to the backend (starting with happi JSON). Non-editable backends (e.g., BCS) hide editing UI.

## Motivation

The device panel currently lists devices but provides no way to modify their configurations. Users must drop to the happi CLI or edit JSON by hand. This feature brings device management into the LUCID UI and makes it accessible to the Claude agent via MCP.

---

## 1. Backend Write-Through

### 1.1 `DeviceBackend` ABC Changes (`devices/base.py`)

Add an `is_editable` property (default `False`):

```python
@property
def is_editable(self) -> bool:
    return False
```

All other backends will be expected to conform to the same data structure as happi.

### 1.2 `HappiBackend` Changes (`devices/backends/happi.py`)

**`is_editable`** — returns `True`.

**`update_device(device)`** — after updating the in-memory `_devices` dict:
1. Look up the happi item by name via `self._client.search(name=device.name)`
2. Update happi item fields from the `DeviceInfo` (prefix, group, active, display_name, icon_override, beamline, metadata, etc.)
3. Call `item.save()` to write through to the JSON file

**`add_device(device)`** — create a new happi item:
1. Create appropriate happi container (e.g., `OphydItem` or `HappiItem`)
2. Populate fields from the `DeviceInfo`
3. Call `self._client.add_item(item)` then backend save
4. Add to in-memory `_devices` dict
5. Return `True`

**`remove_device(device_id)`** — delete from happi:
1. Look up device name from `_devices`
2. Call `self._client.remove_item(name)`
3. Remove from in-memory `_devices`, `_configurations`, `_maintenance` dicts
4. Return `True`

**Auto-init JSON database** — in `connect()`, if `self._path` is set and the file does not exist:
1. Create the file with happi's `JSONBackend` (which initializes an empty DB structure)
2. Show a toast notification via the app's notification system: "Created new device database at {path}"
3. Proceed normally (empty device list)

### 1.3 `DeviceInfo` Model Changes (`devices/model.py`)

Add three first-class fields to `DeviceInfo`:

```python
display_name: str = ""        # User-facing label (falls back to name if empty)
icon_override: str = ""       # Enum string for icon override (empty = auto from category)
group: str = ""               # User-defined grouping
```

These are stored in happi as extra/extraneous fields on the happi item and round-tripped through the metadata mapping in `_add_device_from_result()`.

### 1.4 `DeviceCatalog` Changes (`devices/catalog.py`)

- Expose `is_editable` from the current backend
- Ensure `add_device()`, `update_device()`, `remove_device()` emit the existing Qt signals (`device_added`, `device_removed`, `device_updated`)
- The tree model already listens to these signals and refreshes

---

## 2. Device Edit Dialog

### 2.1 New File: `ui/dialogs/device_edit_dialog.py`

A `QDialog` subclass using pyqtgraph's `ParameterTree` for a procedural, data-driven UI. Supports two modes: **edit** (existing device) and **create** (new device).

### 2.2 Dialog Layout

- **Title bar:** "Edit Device: {name}" or "Add New Device"
- **ParameterTree** filling the main area with these groups:

**Identity group:**
| Field | Type | Edit Mode | Create Mode |
|-------|------|-----------|-------------|
| `name` | str | read-only | editable, required |
| `display_name` | str | editable | editable |
| `device_class` | str | read-only | editable, required |

**Connection group:**
| Field | Type | Notes |
|-------|------|-------|
| `prefix` | str | EPICS PV prefix or identifier |
| `beamline` | str | Beamline identifier |

**Organization group:**
| Field | Type | Notes |
|-------|------|-------|
| `group` | str | User-defined grouping |
| `icon_override` | list/enum | Predefined icon set, empty = auto |
| `active` | bool | Enable/disable device |

**Extra Fields group:**
- Displays existing key-value pairs from `metadata` as editable rows
- "Add Field" action to append a new key-value pair
- Each extra field has a remove action/button

### 2.3 Behavior

- Opened from the device panel context menu (Edit or Add New Device)
- In edit mode: populated from the device's current fields (read from `DeviceInfo` + happi item metadata)
- On Save: validates required fields (name, device_class for new), calls `DeviceCatalog.update_device()` or `DeviceCatalog.add_device()`
- On Cancel: discards all changes
- Validation: name uniqueness check (on create), non-empty required fields
- Follows existing ParameterTree patterns from `plan_config.py`

---

## 3. Context Menu & Device Panel Changes

### 3.1 Context Menu (`ui/panels/device_panel.py`)

Set `CustomContextMenu` policy on the `QTreeView` (same pattern as `queue_view.py` and `logging_panel.py`).

**Right-click on a device node** (only when `backend.is_editable` is `True` for edit/delete actions):
- Edit... → opens `DeviceEditDialog` in edit mode
- Enable / Disable (label toggles based on current `active` state)
- *(separator)*
- Copy Name
- Copy Prefix
- *(separator)*
- Delete → confirmation dialog ("Delete device '{name}'? This cannot be undone."), then `DeviceCatalog.remove_device()`
- *(separator)*
- Add New Device... → opens `DeviceEditDialog` in create mode

**Right-click on empty space or non-device node:**
- Add New Device... (if `backend.is_editable`)

Copy Name and Copy Prefix are always available regardless of `is_editable`.

### 3.2 Inactive Device Rendering (`ui/models/device_tree.py`)

- `DeviceTreeModel` loads all devices including inactive ones (`active_only=False`)
- `data()` returns a grey foreground color (`Qt.ForegroundRole`) when `device_info.active == False`
- Inactive devices are never instantiated (not queued for background connection)

### 3.3 Inactive Device Interaction (`ui/panels/device_panel.py`)

- Single-click on inactive device: shows a static "Device Inactive" widget in the Control tab, Info tab still shows metadata (read-only)
- No ophyd interactions attempted for inactive devices

---

## 4. MCP Tool

### 4.1 New Tool: `ncs_manage_device`

Added to the existing `DeviceToolPlugin` in `plugins/tools/device_tools.py` (or as a separate plugin if preferred — but co-locating with existing device tools is cleaner).

**Tool schema:**

```json
{
  "name": "ncs_manage_device",
  "description": "Add, remove, update, enable, or disable a device in the catalog. Requires an editable backend.",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["add", "remove", "update", "enable", "disable"],
        "description": "The management action to perform"
      },
      "name": {
        "type": "string",
        "description": "Device name (identifier for all actions)"
      },
      "fields": {
        "type": "object",
        "description": "Fields to set (for add/update). Keys: name, display_name, device_class, prefix, beamline, group, icon_override, active, plus any extra metadata fields.",
        "additionalProperties": true
      }
    },
    "required": ["action", "name"]
  }
}
```

**Action behavior:**
- **`add`**: requires `fields` with at least `device_class`. Creates `DeviceInfo`, calls `DeviceCatalog.add_device()`.
- **`remove`**: looks up device by name, calls `DeviceCatalog.remove_device()`.
- **`update`**: looks up device by name, applies `fields` changes, calls `DeviceCatalog.update_device()`. All fields including `device_class` are writable via MCP (takes effect on restart).
- **`enable`**: sets `active=True`, calls update.
- **`disable`**: sets `active=False`, calls update.

**Shares the same code path as the UI** — both go through `DeviceCatalog` → `DeviceBackend` write-through.

**Error cases:**
- Backend not editable → error with message
- Device not found (for update/remove/enable/disable) → error
- Missing required fields (for add) → error
- Duplicate name (for add) → error

### 4.2 Threading

Follows the existing pattern in `device_tools.py`: uses `run_on_main_thread()` to ensure catalog operations happen on the Qt main thread.

---

## 5. Files Changed

### New Files
| File | Purpose |
|------|---------|
| `ui/dialogs/device_edit_dialog.py` | ParameterTree-based edit/create dialog |

### Modified Files
| File | Changes |
|------|---------|
| `devices/base.py` | Add `is_editable` property (default `False`) |
| `devices/backends/happi.py` | Write-through for update/add/remove, auto-init JSON DB |
| `devices/model.py` | Add `display_name`, `icon_override`, `group` fields to `DeviceInfo` |
| `devices/catalog.py` | Expose `is_editable`, ensure signals emit on add/remove/update |
| `ui/panels/device_panel.py` | Context menu, edit dialog integration, inactive device click handling |
| `ui/models/device_tree.py` | Load inactive devices, grey foreground for inactive, skip instantiation |
| `plugins/tools/device_tools.py` | Add `ncs_manage_device` tool |

---

## 6. Out of Scope

- Editing devices from non-happi backends (BCS, mock) — they return `is_editable = False`
- Undo/redo for device edits
- Bulk editing of multiple devices at once
- Device class validation (checking that a `device_class` string is importable)
- Icon override enum definition (placeholder for now — will be defined during implementation)
