# Device Connection and Initialization Fixes

**Date:** 2026-04-05
**Status:** Approved

## Problem Statement

Four issues with the device connection and initialization sequence:

1. **Slow device connection** — Even when all devices CAN connect, they appear to connect roughly in spawn order rather than in parallel. Root cause: caproto's `SharedBroadcaster` search thread uses exponential backoff between search rounds. When device threads independently create PVs, they trickle into the search queue, and PVs registered later wait through progressively longer backoff intervals.

2. **Camera widget blocks main thread** — Selecting a camera device (e.g., SimDetector) freezes the UI. `_connect_signals()` in `camera/base.py` calls `hasattr(cam, attr)` which triggers ophyd's lazy component descriptor, calling `wait_for_connection()` synchronously on the main thread. `signal.get()` and `signal.subscribe()` also block.

3. **Unclear Refresh vs Reconnect buttons** — Refresh rebuilds the tree from the catalog (no connection attempts). Reconnect retries failed devices. The distinction is not obvious to users, and "refresh without reconnecting" is not a useful standalone operation.

4. **Blank rows in device tree** — `_on_device_connected()` in `device_tree.py` appends children to the data structure before calling `beginInsertRows()`. Qt requires notification before mutation. The misordering causes phantom/blank rows.

## Design

### Issue 1: Two-phase device connection

**Files:** `connection_manager.py`, `happi.py`

Replace the current "one thread per device doing instantiation + wait" with a two-phase approach:

**Phase 1 — Instantiation (single background thread):**
- A single `QThreadFuture` runs an `_instantiate_all()` method
- Loops through every `(device_info, happi_result)` pair and calls `happi_result.get()`
- This is non-blocking per-call — ophyd's `__init__` creates PV objects and registers them with caproto's search queue but does not wait for network responses
- Each successful instantiation stores the ophyd device and emits `device_connecting` for UI progress
- Failed instantiations are recorded but do not stop the loop
- By the end of this phase, ALL PV names are registered with the broadcaster's search queue

**Phase 2 — Connection waiting (parallel threads):**
- After all devices are instantiated, spawn one `QThreadFuture` per device
- Each thread only calls `ophyd_device.wait_for_connection(timeout=...)`
- Same callback signals as today (`device_connected` / `device_failed`)

**Why this works:** The broadcaster's `_retry_unanswered_searches` thread sends ALL queued PV names in its first few search rounds (batched into 1472-byte UDP datagrams). Responses arrive based on network latency, not registration order. The exponential backoff only affects *retries* of truly unanswered searches, not the initial burst.

**`_start_background_connections()`** in `happi.py` calls the new two-phase method instead of the old `connect_all`.

### Issue 2: Camera widget async signal connection

**Files:** `camera/base.py`

Move the body of `_connect_signals()` to a background thread:

1. **`_connect_signals()` (main thread):** Sets UI to "Connecting..." state, spawns a `QThreadFuture` running `_connect_signals_background()`. Stores a reference to the thread for cancellation.

2. **`_connect_signals_background()` (background thread):** Performs `hasattr(cam, attr)` checks, `getattr(cam, attr)`, `signal.get()` for initial values, and `signal.subscribe()` calls. Collects results into a plain dict `{attr: initial_value}` and a list of `(signal, sub_id)` tuples.

3. **Completion callback (main thread):** Stores subscriptions in `self._subscriptions`, populates `self._values`, updates status indicator to "Connected", calls display update methods.

4. **Error callback (main thread):** Sets status to "Disconnected", logs the error.

5. **Cancellation:** If the user selects a different device while background connect is in flight, `_disconnect_signals()` / `set_items()` cancels the pending thread via the stored reference.

### Issue 3: Merge Refresh and Reconnect

**Files:** `device_panel.py`

Replace the two toolbar buttons with a single **"Sync"** button. Handler:

1. Resets failed device tracking on all backends
2. Spawns a `QThreadFuture` calling `catalog.reconnect_failed_devices(timeout=5.0)`
3. On completion, calls `self._model.refresh()` to rebuild the tree

Always does both operations: retry failed connections, then rebuild the tree with current state.

### Issue 4: Fix Qt model update ordering

**Files:** `device_tree.py`

In `_on_device_connected()`, fix the `beginInsertRows`/`endInsertRows` ordering:

1. Build children into a temporary detached parent via `_add_components(temp_item, device.ophyd_device)`
2. Call `beginInsertRows()` with the correct row count
3. Reparent children from temp to the real item
4. Call `endInsertRows()`

```python
if device.ophyd_device is not None:
    temp_item = DeviceTreeItem("temp", NodeType.ROOT)
    self._add_components(temp_item, device.ophyd_device)
    if temp_item.children:
        self.beginInsertRows(self.index(row, 0), 0, len(temp_item.children) - 1)
        for child in temp_item.children:
            child.parent_item = item
        item.children = temp_item.children
        self.endInsertRows()
```

## File Change Summary

| File | Change |
|------|--------|
| `ncs/src/lucid/devices/connection_manager.py` | Add two-phase `connect_all` with `_instantiate_all()` + parallel wait |
| `ncs/src/lucid/devices/backends/happi.py` | Call new two-phase method from `_start_background_connections()` |
| `ncs/src/lucid/ui/widgets/camera/base.py` | Move `_connect_signals()` body to `QThreadFuture` |
| `ncs/src/lucid/ui/panels/device_panel.py` | Merge Refresh/Reconnect into single Sync button |
| `ncs/src/lucid/ui/models/device_tree.py` | Fix `beginInsertRows`/`endInsertRows` ordering |

## Testing Notes

- Issue 1: Verify with logging that all PV names are registered before wait threads start. Compare device connection times before/after.
- Issue 2: Select SimDetector (or any camera with unreachable PVs) and verify UI stays responsive.
- Issue 3: Verify Sync button both retries failed devices and refreshes the tree.
- Issue 4: Expand a device after it connects and verify no blank rows appear.
