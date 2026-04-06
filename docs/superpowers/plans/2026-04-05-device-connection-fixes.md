# Device Connection and Initialization Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four device connection/initialization issues: search serialization causing slow connections, camera widget blocking the main thread, confusing dual buttons, and blank tree rows.

**Architecture:** Four independent fixes touching different layers. Task 1 restructures the connection manager to separate ophyd instantiation (PV registration) from connection waiting. Task 2 moves camera signal setup off the main thread. Task 3 merges two toolbar buttons. Task 4 fixes Qt model mutation ordering.

**Tech Stack:** Python 3.10+, PySide6, ophyd, caproto, pytest, pytest-qt

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/lucid/devices/connection_manager.py` | Modify | Add two-phase `connect_all_phased` method |
| `src/lucid/devices/backends/happi.py` | Modify | Call `connect_all_phased` from `_start_background_connections` |
| `src/lucid/ui/widgets/camera/base.py` | Modify | Move `_connect_signals` body to background thread |
| `src/lucid/ui/panels/device_panel.py` | Modify | Merge Refresh + Reconnect into Sync |
| `src/lucid/ui/models/device_tree.py` | Modify | Fix `beginInsertRows`/`endInsertRows` ordering |
| `tests/test_connection_manager.py` | Create | Tests for two-phase connection |
| `tests/test_device_tree_model.py` | Create | Tests for tree model row insertion |

---

### Task 1: Two-phase device connection — connection manager

**Files:**
- Modify: `src/lucid/devices/connection_manager.py:271-288` (add `connect_all_phased`)
- Create: `tests/test_connection_manager.py`

- [ ] **Step 1: Write tests for the two-phase connection**

Create `tests/test_connection_manager.py`:

```python
"""Tests for DeviceConnectionManager two-phase connection."""

import time
import threading
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtCore import QCoreApplication

from lucid.devices.connection_manager import (
    ConnectionResult,
    ConnectionState,
    DeviceConnectionManager,
)


@pytest.fixture
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


@pytest.fixture
def manager(qapp):
    DeviceConnectionManager.reset_instance()
    mgr = DeviceConnectionManager.get_instance()
    yield mgr
    DeviceConnectionManager.reset_instance()


def _make_device_and_result(name="dev", connect_time=0.0):
    """Create a mock DeviceInfo and happi result pair.

    Args:
        name: Device name.
        connect_time: Seconds the mock wait_for_connection should sleep.
    """
    device_info = MagicMock()
    device_info.id = uuid4()
    device_info.name = name

    ophyd_device = MagicMock()
    ophyd_device.wait_for_connection = MagicMock(
        side_effect=lambda timeout=5.0: time.sleep(connect_time)
    )

    happi_result = MagicMock()
    happi_result.get.return_value = ophyd_device

    return device_info, happi_result, ophyd_device


class TestConnectAllPhased:
    def test_instantiation_before_wait(self, manager, qapp):
        """All happi_result.get() calls should complete before any
        wait_for_connection() calls start."""
        call_log = []  # (event, device_name, timestamp)

        devices = []
        for i in range(3):
            info, result, ophyd = _make_device_and_result(f"dev{i}")

            orig_get = result.get
            def make_get_side_effect(n, og):
                def side_effect():
                    call_log.append(("get", n, time.monotonic()))
                    return og()
                return side_effect
            result.get.side_effect = make_get_side_effect(f"dev{i}", orig_get)

            orig_wait = ophyd.wait_for_connection
            def make_wait_side_effect(n, ow):
                def side_effect(timeout=5.0):
                    call_log.append(("wait", n, time.monotonic()))
                    return ow(timeout=timeout)
                return side_effect
            ophyd.wait_for_connection.side_effect = make_wait_side_effect(
                f"dev{i}", orig_wait
            )

            devices.append((info, result))

        # Run phased connection
        manager.connect_all_phased(devices, timeout=5.0)

        # Wait for all to finish
        deadline = time.monotonic() + 10.0
        while manager._pending_count > 0 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)

        # Verify ordering: all "get" calls before any "wait" call
        get_times = [t for ev, _, t in call_log if ev == "get"]
        wait_times = [t for ev, _, t in call_log if ev == "wait"]

        assert len(get_times) == 3, f"Expected 3 get calls, got {len(get_times)}"
        assert len(wait_times) == 3, f"Expected 3 wait calls, got {len(wait_times)}"
        assert max(get_times) <= min(wait_times), (
            "All instantiations must finish before any wait_for_connection starts"
        )

    def test_emits_signals_on_success(self, manager, qapp):
        """device_connected should be emitted for each successful device."""
        info, result, ophyd = _make_device_and_result("motor1")

        connected_ids = []
        manager.device_connected.connect(
            lambda r: connected_ids.append(r.device_name)
        )

        manager.connect_all_phased([(info, result)], timeout=5.0)

        deadline = time.monotonic() + 10.0
        while not connected_ids and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)

        assert "motor1" in connected_ids

    def test_failed_instantiation_does_not_block_others(self, manager, qapp):
        """If happi_result.get() raises for one device, others still connect."""
        good_info, good_result, _ = _make_device_and_result("good")
        bad_info, bad_result, _ = _make_device_and_result("bad")
        bad_result.get.side_effect = RuntimeError("import error")

        connected = []
        failed = []
        manager.device_connected.connect(lambda r: connected.append(r.device_name))
        manager.device_failed.connect(lambda r: failed.append(r.device_name))

        manager.connect_all_phased(
            [(bad_info, bad_result), (good_info, good_result)], timeout=5.0
        )

        deadline = time.monotonic() + 10.0
        while len(connected) + len(failed) < 2 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)

        assert "good" in connected
        assert "bad" in failed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_connection_manager.py -v`
Expected: FAIL — `AttributeError: 'DeviceConnectionManager' object has no attribute 'connect_all_phased'`

- [ ] **Step 3: Implement `connect_all_phased` in connection manager**

In `src/lucid/devices/connection_manager.py`, add the following method after the existing `connect_all` method (after line 288):

```python
    def connect_all_phased(
        self,
        devices: list[tuple[DeviceInfo, Any]],
        timeout: float | None = None,
    ) -> None:
        """Connect devices in two phases to avoid caproto search serialization.

        Phase 1 (single thread): Instantiate all ophyd devices via
        ``happi_result.get()``.  This registers every PV name with
        caproto's broadcaster so the first search round covers all PVs.

        Phase 2 (parallel threads): Call ``wait_for_connection`` on each
        device concurrently.

        Args:
            devices: List of (DeviceInfo, happi_result) tuples.
            timeout: Optional per-device timeout override in seconds.
        """
        if not devices:
            return

        logger.info(
            "Starting phased connection for {} devices", len(devices)
        )

        # Track pending count for the whole batch up front
        with self._pending_lock:
            self._pending_count += len(devices)

        # Mark all as CONNECTING and emit signals
        for device_info, _ in devices:
            self._connection_states[device_info.id] = ConnectionState.CONNECTING
            self.device_connecting.emit(str(device_info.id))

        def _phase1_instantiate():
            """Instantiate all ophyd devices on a single thread."""
            instantiated: list[tuple[DeviceInfo, Any, float]] = []
            failed: list[tuple[DeviceInfo, str, float]] = []

            for device_info, happi_result in devices:
                start = time.monotonic()
                try:
                    if not hasattr(happi_result, "get"):
                        raise ValueError("happi_result has no get() method")
                    ophyd_device = happi_result.get()
                    if ophyd_device is None:
                        raise ValueError("happi_result.get() returned None")
                    elapsed = (time.monotonic() - start) * 1000
                    instantiated.append((device_info, ophyd_device, elapsed))
                    logger.debug(
                        "Instantiated '{}' in {:.1f}ms",
                        device_info.name,
                        elapsed,
                    )
                except Exception as e:
                    elapsed = (time.monotonic() - start) * 1000
                    failed.append((device_info, str(e), elapsed))
                    logger.warning(
                        "Failed to instantiate '{}': {}", device_info.name, e
                    )

            return instantiated, failed

        def _on_phase1_done(result):
            """Start phase 2: parallel wait_for_connection threads."""
            instantiated, failed = result

            # Emit failures immediately
            for device_info, error, elapsed in failed:
                fail_result = ConnectionResult(
                    device_id=device_info.id,
                    device_name=device_info.name,
                    state=ConnectionState.FAILED,
                    error=error,
                    elapsed_ms=elapsed,
                )
                self._connection_states[device_info.id] = ConnectionState.FAILED
                self._connection_results[device_info.id] = fail_result
                self.device_failed.emit(fail_result)
                self._check_batch_complete()

            # Start parallel wait threads for successfully instantiated devices
            for device_info, ophyd_device, inst_elapsed in instantiated:
                effective_timeout = timeout or self.get_device_timeout(
                    device_info.id
                )
                thread = QThreadFuture(
                    self._do_wait_for_connection,
                    device_info,
                    ophyd_device,
                    effective_timeout,
                    inst_elapsed,
                    callback_slot=self._on_connection_complete,
                    except_slot=self._on_connection_error,
                    name=f"wait_{device_info.name}",
                    key=f"device_connect_{device_info.id}",
                )
                self._active_threads[device_info.id] = thread
                thread.start()

        def _on_phase1_error(error):
            """Handle unexpected error in the instantiation phase."""
            logger.error("Phase 1 instantiation failed: {}", error)
            # Fail all devices in the batch
            for device_info, _ in devices:
                fail_result = ConnectionResult(
                    device_id=device_info.id,
                    device_name=device_info.name,
                    state=ConnectionState.FAILED,
                    error=str(error),
                )
                self._connection_states[device_info.id] = ConnectionState.FAILED
                self._connection_results[device_info.id] = fail_result
                self.device_failed.emit(fail_result)
                self._check_batch_complete()

        phase1_thread = QThreadFuture(
            _phase1_instantiate,
            callback_slot=_on_phase1_done,
            except_slot=_on_phase1_error,
            name="phased-instantiate-all",
        )
        phase1_thread.start()

    def _do_wait_for_connection(
        self,
        device_info: DeviceInfo,
        ophyd_device: Any,
        timeout: float,
        inst_elapsed_ms: float,
    ) -> ConnectionResult:
        """Wait for an already-instantiated ophyd device to connect.

        Args:
            device_info: The DeviceInfo.
            ophyd_device: Already-instantiated ophyd device.
            timeout: Connection timeout in seconds.
            inst_elapsed_ms: Time already spent on instantiation.

        Returns:
            ConnectionResult with success/failure info.
        """
        device_id = device_info.id
        device_name = device_info.name
        start_time = time.monotonic()

        try:
            if hasattr(ophyd_device, "wait_for_connection"):
                ophyd_device.wait_for_connection(timeout=timeout)
            elif hasattr(ophyd_device, "connected"):
                deadline = time.monotonic() + timeout
                while not ophyd_device.connected and time.monotonic() < deadline:
                    time.sleep(0.1)
                if not ophyd_device.connected:
                    raise TimeoutError(
                        f"Device did not connect within {timeout}s"
                    )

            elapsed = inst_elapsed_ms + (time.monotonic() - start_time) * 1000
            logger.info(
                "Device '{}' connected in {:.1f}ms (inst+wait)",
                device_name,
                elapsed,
            )
            return ConnectionResult(
                device_id=device_id,
                device_name=device_name,
                state=ConnectionState.CONNECTED,
                ophyd_device=ophyd_device,
                elapsed_ms=elapsed,
            )

        except TimeoutError as e:
            elapsed = inst_elapsed_ms + (time.monotonic() - start_time) * 1000
            logger.warning(
                "Device '{}' timed out after {:.1f}ms: {}",
                device_name,
                elapsed,
                e,
            )
            return ConnectionResult(
                device_id=device_id,
                device_name=device_name,
                state=ConnectionState.TIMEOUT,
                error=str(e),
                elapsed_ms=elapsed,
            )

        except Exception as e:
            elapsed = inst_elapsed_ms + (time.monotonic() - start_time) * 1000
            logger.warning(
                "Device '{}' connection failed after {:.1f}ms: {}",
                device_name,
                elapsed,
                e,
            )
            return ConnectionResult(
                device_id=device_id,
                device_name=device_name,
                state=ConnectionState.FAILED,
                error=str(e),
                elapsed_ms=elapsed,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_connection_manager.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/devices/connection_manager.py tests/test_connection_manager.py
git commit -m "feat: add two-phase connect_all_phased to DeviceConnectionManager

Separates ophyd instantiation (PV registration) from connection
waiting.  Phase 1 runs all happi_result.get() calls on a single
thread so caproto's broadcaster sees every PV name before the
first search retry round.  Phase 2 spawns parallel threads for
wait_for_connection()."
```

---

### Task 2: Wire happi backend to use phased connection

**Files:**
- Modify: `src/lucid/devices/backends/happi.py:347`

- [ ] **Step 1: Change `_start_background_connections` to call `connect_all_phased`**

In `src/lucid/devices/backends/happi.py`, change line 347 from:

```python
            manager.connect_all(to_connect, timeout=self._connection_timeout)
```

to:

```python
            manager.connect_all_phased(to_connect, timeout=self._connection_timeout)
```

- [ ] **Step 2: Run existing tests**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/ -v`
Expected: All tests pass (no existing tests depend on `connect_all` being called).

- [ ] **Step 3: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/devices/backends/happi.py
git commit -m "feat: use phased connection in HappiBackend

Switches _start_background_connections from connect_all to
connect_all_phased for faster device discovery."
```

---

### Task 3: Async camera signal connection

**Files:**
- Modify: `src/lucid/ui/widgets/camera/base.py:386,442-460,649-709,712-719`

- [ ] **Step 1: Add `_connect_thread` attribute in `__init__`**

In `src/lucid/ui/widgets/camera/base.py`, after line 386 (`self._subscriptions: list[tuple[Any, int]] = []`), add:

```python
        self._connect_thread: QThreadFuture | None = None
```

Also add the import near the top of the file (with other lucid imports):

```python
from lucid.utils.threads import QThreadFuture
```

- [ ] **Step 2: Refactor `_connect_signals` into main-thread launcher + background worker**

Replace the `_connect_signals` method (lines 649-709) with:

```python
    def _connect_signals(self) -> None:
        """Subscribe to ophyd device signals in the background.

        Spawns a QThreadFuture so that hasattr/getattr/get/subscribe
        calls (which may trigger ophyd lazy instantiation and caproto
        wait_for_connection) do not block the UI thread.
        """
        self._cancel_connect_thread()
        self._disconnect_signals()

        if self._device is None or not hasattr(self._device, "cam"):
            self._status_indicator.set_state("disconnected")
            self._status_label.setText("No cam component")
            return

        self._status_indicator.set_state("disconnected")
        self._status_label.setText("Connecting...")

        cam = self._device.cam

        def _background_connect():
            """Run on background thread — collect signal info."""
            signal_map = {
                "acquire_time": ("acquire_time", "acquire_time_rbv"),
                "num_images": ("num_images", "num_images_rbv"),
                "image_mode": ("image_mode", "image_mode_rbv"),
                "acquire": ("acquire",),
                "detector_state": ("detector_state",),
            }

            initial_values = {}
            subscriptions = []

            def make_callback(names: tuple[str, ...]):
                def callback(value, **kwargs):
                    for name in names:
                        self._on_value_changed(name, value)
                return callback

            for attr, names in signal_map.items():
                if hasattr(cam, attr):
                    signal = getattr(cam, attr)

                    try:
                        value = signal.get()
                        for name in names:
                            initial_values[name] = value
                    except Exception as e:
                        logger.debug(
                            "Failed to get initial value for {}: {}", attr, e
                        )

                    try:
                        sub_id = signal.subscribe(make_callback(names))
                        subscriptions.append((signal, sub_id))
                    except Exception as e:
                        logger.debug(
                            "Failed to subscribe to {}: {}", attr, e
                        )

            return initial_values, subscriptions

        def _on_connected(result):
            """Main thread callback — apply results to UI."""
            initial_values, subscriptions = result
            self._subscriptions = subscriptions
            self._values.update(initial_values)

            self._status_indicator.set_state("on")
            self._status_label.setText("Connected")
            self._set_controls_enabled(True)

            self._update_acquire_time_display()
            self._update_num_images_display()
            self._update_image_mode_display()

            self._connect_thread = None

        def _on_error(error):
            """Main thread callback — handle failure."""
            logger.warning("Camera signal connection failed: {}", error)
            self._status_indicator.set_state("disconnected")
            self._status_label.setText("Connection failed")
            self._connect_thread = None

        self._connect_thread = QThreadFuture(
            _background_connect,
            callback_slot=_on_connected,
            except_slot=_on_error,
            name="camera-connect-signals",
        )
        self._connect_thread.start()

    def _cancel_connect_thread(self) -> None:
        """Cancel any in-flight background signal connection."""
        if self._connect_thread is not None and self._connect_thread.running:
            self._connect_thread.cancel()
            self._connect_thread = None
```

- [ ] **Step 3: Update `set_items` to cancel on device change**

Replace `set_items` (lines 442-462) with:

```python
    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the camera device to control.

        Uses the ophyd device directly for all operations, providing a uniform
        interface regardless of whether the device is backed by EPICS or
        simulated signals.
        """
        self._items = items

        if items and len(items) == 1:
            item = items[0]
            self._device = item.ophyd_obj
            self._update_image_view()
            self._connect_signals()
            self._start_updates()
            self._name_label.setText(item.name)
        else:
            self._device = None
            self._cancel_connect_thread()
            self._disconnect_signals()
            self._stop_updates()
            self._clear_display()
```

- [ ] **Step 4: Update `_disconnect_signals` to also cancel thread**

Replace `_disconnect_signals` (lines 712-719) with:

```python
    def _disconnect_signals(self) -> None:
        """Disconnect all ophyd signal subscriptions."""
        self._cancel_connect_thread()
        for signal, sub_id in self._subscriptions:
            try:
                signal.unsubscribe(sub_id)
            except Exception:
                pass
        self._subscriptions.clear()
        self._values.clear()
```

- [ ] **Step 5: Smoke test**

Run the application and select a camera device. Verify:
- UI shows "Connecting..." immediately (no freeze)
- Status changes to "Connected" once signals resolve
- Selecting a different device while connecting cancels the old thread

- [ ] **Step 6: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/widgets/camera/base.py
git commit -m "fix: move camera signal connection off the main thread

_connect_signals now spawns a QThreadFuture for the hasattr/get/
subscribe calls that can trigger ophyd lazy instantiation and
block on caproto wait_for_connection.  UI shows Connecting...
state and stays responsive."
```

---

### Task 4: Merge Refresh and Reconnect into Sync

**Files:**
- Modify: `src/lucid/ui/panels/device_panel.py:352-438`

- [ ] **Step 1: Replace toolbar creation and handlers**

In `src/lucid/ui/panels/device_panel.py`, replace the `_create_toolbar` method (lines 352-395) with:

```python
    def _create_toolbar(self) -> QToolBar:
        """Create the panel toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        # Sync: reconnect failed devices + refresh tree
        sync_action = QAction("Sync", self)
        sync_action.setToolTip(
            "Retry failed device connections and refresh the tree"
        )
        sync_action.triggered.connect(self._sync_devices)
        toolbar.addAction(sync_action)

        toolbar.addSeparator()

        # Expand all
        expand_action = QAction("Expand All", self)
        expand_action.triggered.connect(lambda: self._tree_view.expandAll())
        toolbar.addAction(expand_action)

        # Collapse all
        collapse_action = QAction("Collapse", self)
        collapse_action.triggered.connect(lambda: self._tree_view.collapseAll())
        toolbar.addAction(collapse_action)

        toolbar.addSeparator()

        # Expand to depth
        depth1_action = QAction("Depth 1", self)
        depth1_action.setToolTip("Expand to depth 1 (devices only)")
        depth1_action.triggered.connect(lambda: self._expand_to_depth(0))
        toolbar.addAction(depth1_action)

        depth2_action = QAction("Depth 2", self)
        depth2_action.setToolTip("Expand to depth 2 (devices + components)")
        depth2_action.triggered.connect(lambda: self._expand_to_depth(1))
        toolbar.addAction(depth2_action)

        return toolbar
```

Replace the `_refresh` and `_reconnect_failed` methods (lines 402-438) with:

```python
    def _sync_devices(self) -> None:
        """Retry failed connections and refresh the device tree."""
        from lucid.devices import DeviceCatalog
        from lucid.utils.threads import QThreadFuture

        catalog = DeviceCatalog.get_instance()

        # Reset permanently failed tracking so we retry everything
        for backend in catalog.backends.values():
            if hasattr(backend, "reset_failed_devices"):
                backend.reset_failed_devices()

        def _do_reconnect():
            return catalog.reconnect_failed_devices(timeout=5.0)

        def _on_done(result):
            connected, failed = result
            self._model.refresh()
            logger.info(
                "Sync: {} devices connected, {} still offline",
                connected,
                failed,
            )

        thread = QThreadFuture(
            _do_reconnect,
            callback_slot=_on_done,
            name="sync-devices",
        )
        thread.start()
        logger.info("Syncing devices...")
```

- [ ] **Step 2: Smoke test**

Run the application. Verify:
- Toolbar shows a single "Sync" button (no "Refresh" or "Reconnect")
- Clicking Sync retries failed devices AND refreshes the tree on completion
- Other toolbar buttons (Expand All, Collapse, Depth 1, Depth 2) still work

- [ ] **Step 3: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/panels/device_panel.py
git commit -m "fix: merge Refresh and Reconnect into single Sync button

Replaces two confusing toolbar actions with one that retries
failed connections then rebuilds the device tree."
```

---

### Task 5: Fix Qt model row insertion ordering

**Files:**
- Modify: `src/lucid/ui/models/device_tree.py:678-689`
- Create: `tests/test_device_tree_model.py`

- [ ] **Step 1: Write test for correct row insertion**

Create `tests/test_device_tree_model.py`:

```python
"""Tests for DeviceTreeModel row insertion ordering."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtCore import QCoreApplication, QModelIndex

from lucid.devices.model import DeviceInfo, DeviceState, DeviceStatus
from lucid.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType


@pytest.fixture
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


def _make_catalog_with_device():
    """Create a mock catalog with one device that has components."""
    catalog = MagicMock()

    device_id = uuid4()
    device_info = MagicMock(spec=DeviceInfo)
    device_info.id = device_id
    device_info.name = "test_motor"
    device_info.device_class = "ophyd.sim.SynAxis"
    device_info.category = MagicMock()
    device_info.category.value = "motor"
    device_info.metadata = {}
    device_info.active = True

    # Initially not connected
    device_info.ophyd_device = None
    device_info._state = DeviceState(
        device_id=device_id, status=DeviceStatus.CONNECTING, connected=False
    )

    catalog.get_all_devices.return_value = [device_info]
    catalog.get_device.return_value = device_info

    return catalog, device_info, str(device_id)


class TestOnDeviceConnected:
    def test_no_blank_rows_after_connect(self, qapp):
        """Children added by _on_device_connected should not produce blank rows.

        The Qt model must call beginInsertRows BEFORE mutating
        item.children, and endInsertRows AFTER.
        """
        catalog, device_info, device_id_str = _make_catalog_with_device()

        model = DeviceTreeModel(catalog)

        # Verify device is in the model
        assert model.rowCount(QModelIndex()) == 1
        device_index = model.index(0, 0)
        assert model.data(device_index) == "test_motor"

        # No children yet
        assert model.rowCount(device_index) == 0

        # Simulate device connecting: create a mock ophyd device with components
        ophyd_device = MagicMock()
        ophyd_device.component_names = ("readback", "setpoint")
        ophyd_device._signals = {
            "readback": MagicMock(),
            "setpoint": MagicMock(),
        }
        ophyd_device._sig_attrs = {}
        device_info.ophyd_device = ophyd_device

        # Track beginInsertRows/endInsertRows calls
        insert_calls = []
        orig_begin = model.beginInsertRows
        orig_end = model.endInsertRows

        def tracking_begin(*args):
            insert_calls.append(("begin", len(model._root.children[0].children)))
            return orig_begin(*args)

        def tracking_end():
            insert_calls.append(("end", len(model._root.children[0].children)))
            return orig_end()

        model.beginInsertRows = tracking_begin
        model.endInsertRows = tracking_end

        # Fire the connected signal handler
        model._on_device_connected(device_id_str)

        # Children should now exist
        assert model.rowCount(device_index) == 2

        # Verify beginInsertRows was called BEFORE children were attached
        # (child count should be 0 at begin time, 2 at end time)
        assert len(insert_calls) == 2
        assert insert_calls[0] == ("begin", 0), (
            "beginInsertRows must be called before children are attached"
        )
        assert insert_calls[1] == ("end", 2), (
            "endInsertRows must be called after children are attached"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_tree_model.py -v`
Expected: FAIL — the `begin` call will see children already present (the current buggy ordering).

- [ ] **Step 3: Fix the row insertion ordering**

In `src/lucid/ui/models/device_tree.py`, replace lines 684-689:

```python
        # Add new children from the now-connected ophyd device
        if device.ophyd_device is not None:
            self._add_components(item, device.ophyd_device)
            if item.children:
                self.beginInsertRows(self.index(row, 0), 0, len(item.children) - 1)
                self.endInsertRows()
```

with:

```python
        # Add new children from the now-connected ophyd device
        if device.ophyd_device is not None:
            # Build children into a temporary parent to avoid mutating
            # item.children before beginInsertRows (Qt requires notification
            # before data mutation).
            temp_item = DeviceTreeItem("_temp", NodeType.ROOT)
            self._add_components(temp_item, device.ophyd_device)
            if temp_item.children:
                self.beginInsertRows(
                    self.index(row, 0), 0, len(temp_item.children) - 1
                )
                for child in temp_item.children:
                    child.parent_item = item
                item.children = temp_item.children
                self.endInsertRows()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_device_tree_model.py -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/models/device_tree.py tests/test_device_tree_model.py
git commit -m "fix: correct Qt model beginInsertRows/endInsertRows ordering

Children were appended to the data structure before
beginInsertRows was called, causing phantom blank rows.
Now builds children into a temporary parent, notifies Qt,
then attaches them."
```
