# Device Panel Tabs — Design Spec

**Date:** 2026-04-14
**Status:** Approved

## Summary

Restructure the Lightfall Devices panel from a tree-with-detail-splitter layout to a tabbed interface. The panel gets a top-level `QTabWidget` with two permanent tabs ("Favorites" and "All") and dynamically opened device controller tabs. Favorites are persisted per-beamline. Compact motor widgets provide light inline control in the Favorites tab.

## Goals

- Give users quick access to favorited devices with inline control
- Allow full device controllers to be opened in dedicated tabs
- Simplify the panel by removing the detail splitter
- Keep the existing tree view as the primary device discovery/navigation tool

## Non-Goals

- Compact widgets for non-motor device types (future work)
- Visual indicators in the tree for devices with open tabs
- Grid/flow layout for favorites

---

## 1. Panel Layout

DevicePanel's main widget becomes a `QTabWidget`.

- **Tab 0 — "Favorites"**: Permanent, unclosable. Shows compact control widgets for favorited devices.
- **Tab 1 — "All"**: Permanent, unclosable. Contains the device tree view with toolbar and search/filter.
- **Tab 2+ — Device controllers**: Closable. Each shows a full controller widget for one device.

The tab widget uses `setTabsClosable(True)` globally. `tabCloseRequested` ignores close attempts on indices 0 and 1. Close buttons on those tabs are hidden via `tabBar().setTabButton(idx, QTabBar.RightSide, None)`.

The existing detail splitter (Control + Info tabs at the bottom of the current panel) is removed entirely. The toolbar and search/filter row move inside the "All" tab since they only apply there.

---

## 2. "All" Tab — DeviceTreeTab

A new widget class `DeviceTreeTab` that owns:

- The toolbar (sync, expand/collapse, show disabled toggle)
- The search/filter row (search input + kind filter menu)
- The tree view with the existing `DeviceTreeModel` and `DeviceFilterProxyModel`

This is the top half of the current DevicePanel extracted into its own widget. The tree view keeps multi-selection, alternating row colors, and all current column configuration.

**Double-click:** Connected to `doubleClicked` signal. When a device node is double-clicked, it emits `device_open_requested(DeviceTreeItem)`. DevicePanel listens to this to open a controller tab.

**Context menu:** Same as today (edit, enable/disable, copy name, copy prefix, delete) plus:

- **"Add to Favorites" / "Remove from Favorites"** — label toggles based on current favorite state. Emits `favorite_toggled(str, bool)` (device ID, is_favorite). DevicePanel routes this to the favorites system.

---

## 3. Favorites Tab — FavoritesTab

A new widget class `FavoritesTab` that owns:

- A vertical `QScrollArea` containing a `QVBoxLayout` of `CompactMotorWidget` instances
- A placeholder message when no favorites exist ("Right-click a device in the All tab to add favorites")

**Data flow:** FavoritesTab receives a list of favorited device IDs. For each motor device, it creates a `CompactMotorWidget`. Non-motor devices are skipped for now (future extensibility). Widgets are ordered by the order they were favorited.

**Updates:** When a favorite is added/removed, FavoritesTab adds/removes the corresponding widget. When device connection state changes (from `DeviceCatalog` signals), the compact widget updates accordingly.

**Context menu on compact widgets:** Right-click on a `CompactMotorWidget` shows:

- **Open Controller** — opens the full controller tab (same as double-click in "All")
- **Remove from Favorites** — unfavorites the device

---

## 4. CompactMotorWidget

A single horizontal row widget for controlling one motor.

**Layout (left to right):**

1. **Name label** — device name, fixed width, elided if too long
2. **Position readback** — read-only display of current position (OphydLabel)
3. **Jog/Abs toggle** — small QPushButton toggling between "Jog" and "Abs" text. Affects how the setpoint entry is interpreted.
4. **Setpoint entry** — QLineEdit for entering a target position (absolute) or relative distance (jog)
5. **Go button** — sends the move command
6. **Stop button** — stops the motor immediately

**Behavior:**

- **Abs mode:** Go sends the setpoint value as an absolute move
- **Jog mode:** Go sends the setpoint value as a relative move (current position + value)
- Stop calls the motor's stop method
- Readback updates come from the same ophyd signal binding that the existing MotorControlWidget uses
- If the device isn't connected yet, the widget shows a "connecting..." state and requests connection via DeviceCatalog (same on-demand connection pattern as MotorControlWidget)

**Sizing:** Fixed height (~36-40px), stretches horizontally to fill the favorites tab width.

---

## 5. Device Controller Tabs

When a user double-clicks a device in "All", DevicePanel opens a new tab containing the device's controller widget via the existing `ControllerMatcher` system.

**Tab management:**

- Tab label: device name
- Tab icon: same device type icon from the tree (motor/detector/controller icons)
- Closing a tab destroys the controller widget
- DevicePanel tracks open tabs in a `dict[str, QWidget]` mapping device ID to the tab's widget
- Double-clicking an already-open device uses `indexOf(widget)` to find the current index and calls `setCurrentIndex` to focus it
- On `tabCloseRequested`, the widget is looked up via `widget(index)`, removed from the dict, and destroyed

**Tab content:** The controller widget directly — same thing the current detail splitter's "Control" tab showed, but now full-size as a standalone tab. The Info view (DeviceOverviewWidget) is dropped.

---

## 6. Favorites Persistence

Favorites stored via `PreferencesManager` using a beamline-scoped key.

- **Key:** `"device_favorites"` — JSON list of device ID strings, ordered by when they were favorited
- **Beamline scoping:** Added to `BEAMLINE_SPECIFIC_PREFS` set. Switching beamlines automatically gives a different favorites list.
- **Load/save:** FavoritesTab loads on construction, saves whenever a favorite is added or removed. No debouncing needed — small list, infrequent writes.
- **Resilience:** If a favorited device ID no longer exists in the catalog, the compact widget shows a "device not found" state. The stale ID is pruned from the saved list on next save.

---

## 7. File Structure

**New files:**

| File | Class | Purpose |
|------|-------|---------|
| `ncs/src/lightfall/ui/widgets/device_tree_tab.py` | `DeviceTreeTab` | Extracted tree view with toolbar and search/filter |
| `ncs/src/lightfall/ui/widgets/favorites_tab.py` | `FavoritesTab` | Favorites list with compact widgets |
| `ncs/src/lightfall/ui/widgets/compact_motor.py` | `CompactMotorWidget` | Horizontal motor control row |

**Modified files:**

| File | Change |
|------|--------|
| `ncs/src/lightfall/ui/panels/device_panel.py` | Rebuilt as thin tab coordinator — QTabWidget owner, tab open/close/focus logic, wiring between sub-widgets |
| `ncs/src/lightfall/ui/preferences/manager.py` | Add `"device_favorites"` to `BEAMLINE_SPECIFIC_PREFS` |

**No changes to:** device models, catalog, backends, controller matching, panel plugin/registration system.
