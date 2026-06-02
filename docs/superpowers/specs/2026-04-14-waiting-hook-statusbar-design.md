# RunEngine waiting_hook StatusBar Integration

**Date:** 2026-04-14
**Status:** Draft

## Goal

Integrate Bluesky RunEngine's `waiting_hook` with the existing ThreadStatusPlugin in Lightfall's status bar. This surfaces two levels of progress:

1. **Device-level progress** — per-device movement/trigger status from `StatusBase.watch()` callbacks (e.g., "motor1: 45.2/100.0 mm")
2. **Scan-level progress** — overall event count from document stream (e.g., "Scan: 45/200 events")

Both are displayed in the ThreadStatusPlugin's existing overlay popup, extending (not replacing) the current background-thread progress tracking.

## Architecture

```
RunEngine (RE thread)
  |
  | RE.waiting_hook(status_objs)
  v
WaitingHookBridge (QObject, main thread signals)
  |
  | sigDeviceProgress / sigDeviceFinished / sigWaitGroupCleared
  v
ThreadStatusPlugin
  |
  | upsert_device() / mark_device_done() / clear_devices()
  v
_ProgressOverlay (popup with per-device progress bars)
```

## Component 1: WaitingHookBridge

**File:** `ncs/src/lightfall/acquire/engine/waiting_hook.py`

A `QObject` subclass that acts as the `RE.waiting_hook` callable.

### Signals

| Signal | Arguments | When |
|--------|-----------|------|
| `sigDeviceProgress` | `(name: str, current: float, initial: float, target: float, fraction: float)` | Status object `.watch()` callback fires |
| `sigDeviceFinished` | `(name: str)` | Individual status object completes |
| `sigWaitGroupCleared` | *(none)* | `waiting_hook(None)` called — all waits resolved |

### Behavior

**Called with `Set[StatusBase]`:**

1. Iterate status objects
2. For each with `.watch()` support: call `st.watch(callback)` where callback buffers update kwargs
3. For each status, subscribe to completion via `st.add_callback(cb)` (ophyd's `StatusBase` completion callback API) to emit `sigDeviceFinished` when done
4. For non-watchable status objects: emit `sigDeviceProgress` with `fraction=-1` to indicate indeterminate progress (overlay renders these as pulsing/indeterminate progress bars with range 0-0)

**Called with `None`:**

- Emit `sigWaitGroupCleared`
- Clear internal tracking state

### Throttling

The `.watch()` callbacks fire from the RE thread, potentially at high frequency. The bridge coalesces updates:

- Maintains a `dict[str, tuple]` buffer of latest values per device name
- A `QTimer` on the main thread fires at ~10 Hz (100ms interval)
- On each tick, flush all buffered updates as `sigDeviceProgress` emissions
- The buffer write from the RE thread uses a simple `threading.Lock` (the critical section is tiny — just dict assignment)

### Installation

- Bridge instance created in `BlueskyEngine.__init__()` as `self._waiting_bridge`
- Exposed as `BlueskyEngine.waiting_bridge` property (read-only)
- In `_process_queue()`, after `RunEngine(...)` is created: `self._RE.waiting_hook = self._waiting_bridge`

## Component 2: ThreadStatusPlugin Extension

**File:** `ncs/src/lightfall/ui/statusbar/plugins/thread_status.py`

### Overlay Changes

`_ProgressOverlay` gains a second set of tracking dicts for device rows (keyed by `str` device name, alongside existing thread rows keyed by `int` thread ID):

- `_device_rows: dict[str, QWidget]`
- `_device_bars: dict[str, QProgressBar]`
- `_device_labels: dict[str, QLabel]`

New methods:

- `upsert_device(name, current, initial, target, fraction)` — creates or updates a device progress row. If `fraction >= 0`, sets bar range 0-100 and value to `fraction * 100`. If `fraction < 0` (indeterminate), sets bar range 0-0 (pulsing).
- `mark_device_done(name)` — sets bar to 100%, schedules removal after 1 second via `QTimer.singleShot`.
- `clear_devices()` — removes all device rows immediately.

Device rows appear in a visually distinct section below any thread rows. A thin separator line divides the sections when both are present. Device row labels include the device name from the watch callback (e.g., "motor1" or "det1").

### Scan-Level Progress

The plugin subscribes to `engine.sigOutput` to track scan progress:

- On `'start'` document: extract `num_points` (if present), store current scan UID, create a "Scan" row in the overlay
- On `'event'` document: increment event counter, update the "Scan" progress bar (determinate if `num_points` known, indeterminate otherwise)
- On `'stop'` document: mark scan row complete, remove after 1 second

The scan row appears at the top of the overlay, above device and thread rows.

### Status Bar Label

The label text adapts to show scan state:

| State | Label |
|-------|-------|
| No tasks, no scan | *(hidden)* |
| Scan running, no other tasks | `"scanning"` |
| Scan running + N tasks | `"scan + N tasks"` |
| No scan, N tasks | `"N tasks"` |

All variants keep the hourglass prefix.

### Signal Connections

In `connect_signals()`:

```python
# Existing
thread_manager.sigProgress.connect(self._on_progress)
thread_manager.sigFinished.connect(self._on_finished)

# New — RunEngine waiting_hook
from lightfall.acquire import get_engine
engine = get_engine()
if hasattr(engine, 'waiting_bridge'):
    bridge = engine.waiting_bridge
    bridge.sigDeviceProgress.connect(self._on_device_progress)
    bridge.sigDeviceFinished.connect(self._on_device_finished)
    bridge.sigWaitGroupCleared.connect(self._on_wait_cleared)

# New — scan-level progress
engine.sigOutput.connect(self._on_document)
```

Corresponding disconnections in `disconnect_signals()`.

### Device Completion Animation

When `sigDeviceFinished` fires for a device:

1. `_ProgressOverlay.mark_device_done(name)` sets bar to 100%
2. A `QTimer.singleShot(1000, lambda: self._remove_device(name))` schedules removal
3. If `sigWaitGroupCleared` fires before the timer, `clear_devices()` removes everything immediately (cancels pending timers)

## Thread Safety

- **RE thread -> main thread**: The `WaitingHookBridge` is the only crossing point. It uses a lock-protected buffer + QTimer flush, so all signal emissions happen on the main thread. No Qt widget access from the RE thread.
- **Plugin**: Receives only main-thread signals. All widget manipulation is safe.
- **Engine property access**: `get_engine()` returns a singleton; `.waiting_bridge` is set once in `__init__` and never changes. Safe to read from main thread during `connect_signals()`.

## Testing

- **Unit test for WaitingHookBridge**: Mock status objects with/without `.watch()`, verify signals are emitted with correct values after timer flush. Test `None` call emits `sigWaitGroupCleared`.
- **Unit test for overlay**: Verify `upsert_device` / `mark_device_done` / `clear_devices` add/remove widgets correctly.
- **Integration test**: End-to-end with a simulated motor move through the RunEngine, verify progress signals flow through to the overlay.

## Files Changed

| File | Change |
|------|--------|
| `ncs/src/lightfall/acquire/engine/waiting_hook.py` | **New** — WaitingHookBridge class |
| `ncs/src/lightfall/acquire/engine/bluesky.py` | Create bridge in `__init__`, install in `_process_queue`, expose property |
| `ncs/src/lightfall/ui/statusbar/plugins/thread_status.py` | Add device/scan tracking to overlay and plugin |
