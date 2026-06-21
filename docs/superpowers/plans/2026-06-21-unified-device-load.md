# Unified Device-Load Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two divergent device-load paths with one background, concurrent, non-blocking `load metadata → instantiate → connect` pipeline driven through an abstract `DeviceBackend` hook interface.

**Architecture:** `DeviceBackend` exposes three device-layer hooks — `load_metadata()`, `instantiate(info)`, `check_connection(obj, timeout)`. `DeviceCatalog.add_backend()` is the single entry (startup and late): it loads metadata on a worker, then — back on the main thread — registers devices and hands them to `DeviceConnectionManager.connect_devices()`, which instantiates+connects each device concurrently in a bounded pool with per-device status and isolated failures. Orchestration is kicked from the main thread, so all `QThreadFuture` callbacks marshal to main (fixing the dead-worker bug).

**Tech Stack:** Python 3.11, PySide6 (Qt), pydantic models, `lightfall.utils.threads.QThreadFuture`/`ManagedThreadPool`, ophyd (only inside backends).

## Global Constraints

- Tests run with: `lightfall/.venv/Scripts/python -m pytest`; in a worktree set `PYTHONPATH=src` (editable install otherwise resolves to the main checkout).
- `ruff check src/` must stay clean (CI lint gate).
- Public `DeviceCatalog` query API (`get_device`, `get_device_by_name`, `list_devices`, `get_all_devices`) and the signals (`device_added`, `device_connecting`, `device_connected`, `device_failed`, `device_state_changed`, `backend_connected`) keep their current names/signatures — UI consumers must be unaffected.
- `DeviceInfo._ophyd_device` remains the slot for the instantiated object.
- New device-layer code lives only inside backends; `DeviceCatalog` and `DeviceConnectionManager` must not import ophyd/caproto.
- The whole pipeline must be unit-testable with a fake backend (no ophyd/IOC). Real ophyd connection is box-validated, not unit-tested.

---

### Task 1: Backend hook interface (`load_metadata` / `instantiate` / `check_connection`)

**Files:**
- Modify: `src/lightfall/devices/base.py`
- Test: `tests/devices/test_backend_hooks.py` (create)

**Interfaces:**
- Produces:
  - `DeviceBackend.load_metadata(self) -> list[DeviceInfo]` (abstract) — metadata only; no instantiation/connection.
  - `DeviceBackend.instantiate(self, info: DeviceInfo) -> Any` (abstract) — build+return the device object for `info`.
  - `DeviceBackend.check_connection(self, obj: Any, timeout: float) -> bool` (concrete default) — block until `obj` is connected or `timeout`; return connected. Default uses ophyd semantics (`wait_for_connection` / `connected` poll); backends override for other device layers.

- [ ] **Step 1: Write the failing test**

```python
# tests/devices/test_backend_hooks.py
from __future__ import annotations
from lightfall.devices.base import DeviceBackend


# check_connection's body does not use `self`, so call it unbound with None to
# avoid instantiating the ABC (which has many other abstract methods).


def test_check_connection_uses_wait_for_connection():
    class _Obj:
        def __init__(self): self.waited = None
        def wait_for_connection(self, timeout): self.waited = timeout

    obj = _Obj()
    assert DeviceBackend.check_connection(None, obj, timeout=2.0) is True
    assert obj.waited == 2.0


def test_check_connection_polls_connected_flag_true():
    class _Obj:
        connected = True
    assert DeviceBackend.check_connection(None, _Obj(), timeout=1.0) is True


def test_check_connection_polls_connected_flag_times_out_false():
    class _Obj:
        connected = False
    # No wait_for_connection; never connects -> returns False at timeout.
    assert DeviceBackend.check_connection(None, _Obj(), timeout=0.15) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ../../.venv/Scripts/python -m pytest tests/devices/test_backend_hooks.py -v`
Expected: FAIL — `DeviceBackend` has no `check_connection` / `load_metadata` / `instantiate`.

- [ ] **Step 3: Add the hooks to `DeviceBackend`**

```python
# base.py
@abstractmethod
def load_metadata(self) -> list["DeviceInfo"]:
    """Return device metadata only. No instantiation or connection."""

@abstractmethod
def instantiate(self, info: "DeviceInfo") -> Any:
    """Build and return the device object for `info`."""

def check_connection(self, obj: Any, timeout: float) -> bool:
    """Block until `obj` is connected or `timeout` elapses; return connected.

    Default ophyd semantics; override for a different device layer.
    """
    if hasattr(obj, "wait_for_connection"):
        obj.wait_for_connection(timeout=timeout)
        return True
    if hasattr(obj, "connected"):
        import time as _t
        deadline = _t.monotonic() + timeout
        while not obj.connected and _t.monotonic() < deadline:
            _t.sleep(0.05)
        return bool(obj.connected)
    return True  # no connection concept -> treat as connected
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src ../../.venv/Scripts/python -m pytest tests/devices/test_backend_hooks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/devices/base.py tests/devices/test_backend_hooks.py
git commit -m "feat(devices): add abstract backend hooks (load_metadata/instantiate/check_connection)"
```

---

### Task 2: `DeviceConnectionManager.connect_devices` — generic concurrent engine

**Files:**
- Modify: `src/lightfall/devices/connection_manager.py`
- Test: `tests/devices/test_connect_devices.py` (create)

**Interfaces:**
- Consumes: `DeviceBackend.instantiate`, `DeviceBackend.check_connection` (Task 1).
- Produces: `DeviceConnectionManager.connect_devices(self, backend: DeviceBackend, infos: list[DeviceInfo], timeout: float | None = None, max_concurrency: int = 12) -> None` — for each info, runs `instantiate` then `check_connection` in a bounded pool; emits `device_connecting` (start), then `device_connected(ConnectionResult)` / `device_failed(ConnectionResult)`; stores the object on success. Must be called on the main thread; per-device worker callbacks marshal to main.

**Design notes (no placeholder — implementer follows this):**
- Reuse the existing `ConnectionResult` / `ConnectionState` and the `device_connecting`/`device_connected`/`device_failed` signals.
- Bounded concurrency: use a `collections.deque` of pending infos + a counter of in-flight tasks (≤ `max_concurrency`); when a per-device `QThreadFuture` completes (callback on main), start the next pending one. This guarantees one slow/timeout device occupies one slot only and never blocks the queue draining.
- Per-device worker fn `_instantiate_and_connect(backend, info, timeout)` (runs in the pool thread): `obj = backend.instantiate(info)`; `ok = backend.check_connection(obj, timeout)`; return `ConnectionResult(state=CONNECTED if ok else TIMEOUT, ophyd_device=obj, ...)`; on exception return `ConnectionResult(state=FAILED, error=...)`.
- The completion callback (`callback_slot`, delivered to main because the `QThreadFuture` is created while `connect_devices` runs on main) updates `_connection_states`, emits the right signal, decrements in-flight, and pumps the next pending.

- [ ] **Step 1: Write the failing test (fake backend, injected delays/failures)**

```python
# tests/devices/test_connect_devices.py
from __future__ import annotations
import time
from lightfall.devices.base import DeviceBackend
from lightfall.devices.connection_manager import DeviceConnectionManager, ConnectionState
from lightfall.devices.model import DeviceInfo


class _FakeBackend(DeviceBackend):
    name = "fake"
    is_connected = True
    def __init__(self, behaviors): self._b = behaviors  # name -> ("ok"|"fail"|"hang", delay)
    def load_metadata(self): return [DeviceInfo(name=n) for n in self._b]
    def instantiate(self, info): return object()
    def check_connection(self, obj, timeout):
        kind, delay = self._b[self._name_of(obj)] if False else ("ok", 0)
        return True
    # query methods unused here
    def __getattr__(self, n): raise NotImplementedError(n)


def test_connect_devices_one_hang_does_not_block_others(qtbot):
    # 1 device hangs past timeout; 2 connect fast. The fast ones must reach
    # CONNECTED without waiting for the hanging one.
    infos = [DeviceInfo(name=n) for n in ("fast1", "slow", "fast2")]

    class _B(DeviceBackend):
        name = "b"; is_connected = True
        def load_metadata(self): return infos
        def instantiate(self, info): return info  # object stand-in
        def check_connection(self, obj, timeout):
            if obj.name == "slow":
                time.sleep(timeout + 0.5); return False  # times out
            return True
        def __getattr__(self, n): raise NotImplementedError(n)

    mgr = DeviceConnectionManager.get_instance()
    connected, failed = [], []
    mgr.device_connected.connect(lambda r: connected.append(r.device_name))
    mgr.device_failed.connect(lambda r: failed.append(r.device_name))

    mgr.connect_devices(_B(), infos, timeout=0.2, max_concurrency=3)

    qtbot.waitUntil(lambda: set(connected) == {"fast1", "fast2"}, timeout=2000)
    assert "slow" not in connected
    qtbot.waitUntil(lambda: "slow" in failed, timeout=3000)
```

- [ ] **Step 2: Run to verify it fails** — `connect_devices` doesn't exist.

Run: `PYTHONPATH=src ../../.venv/Scripts/python -m pytest tests/devices/test_connect_devices.py -v`
Expected: FAIL (AttributeError: connect_devices).

- [ ] **Step 3: Implement `connect_devices` + `_instantiate_and_connect`** (per Design notes). Use `QThreadFuture(self._instantiate_and_connect, backend, info, timeout, callback_slot=self._on_device_done, ...)` with a bounded scheduler.

- [ ] **Step 4: Run tests** — Expected: PASS (fast ones connect, slow one fails after timeout, no blocking).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/devices/connection_manager.py tests/devices/test_connect_devices.py
git commit -m "feat(devices): generic concurrent connect_devices(backend, infos) engine"
```

---

### Task 3: `DeviceCatalog.add_backend` — single pipeline; remove `add_and_connect_backend`

**Files:**
- Modify: `src/lightfall/devices/catalog.py`
- Test: `tests/devices/test_catalog_add_backend.py` (create); update any test referencing `add_and_connect_backend`.

**Interfaces:**
- Consumes: `connect_devices` (Task 2), `load_metadata` (Task 1).
- Produces: `DeviceCatalog.add_backend(self, backend) -> None` — registers the backend, loads metadata on a worker, then (on main) registers `DeviceInfo`s + emits `device_added`, then calls `DeviceConnectionManager.connect_devices(...)`. `connect()` (startup) calls `add_backend` per configured backend. `add_and_connect_backend` is removed.

- [ ] **Step 1: Failing test** — adding a fake backend registers its devices (emit `device_added`) and drives them to CONNECTED via `connect_devices`, all without blocking; assert `add_and_connect_backend` no longer exists.

```python
def test_add_backend_loads_and_connects(qtbot):
    from lightfall.devices.catalog import DeviceCatalog
    cat = DeviceCatalog()  # or get_instance() with reset
    added = []
    cat.device_added.connect(lambda info: added.append(info.name))
    cat.add_backend(_FakeBackend(["m1", "m2"]))  # fake from Task 2 style
    qtbot.waitUntil(lambda: {"m1", "m2"} <= set(added), timeout=2000)
    assert not hasattr(DeviceCatalog, "add_and_connect_backend")
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement `add_backend` + `_on_metadata_loaded` (main thread) + route `connect()` through `add_backend`; delete `add_and_connect_backend`, `_finish_backend_connect`, `_on_backend_connect_error`.**
- [ ] **Step 4: Run tests** (incl. existing catalog tests; update late-add tests to `add_backend`).
- [ ] **Step 5: Commit** `feat(devices): unify backend loading on DeviceCatalog.add_backend`

---

### Task 4: Migrate `HappiBackend` to hooks

**Files:**
- Modify: `src/lightfall/devices/backends/happi.py`
- Test: `tests/devices/test_happi_backend.py` (update)

**Interfaces:**
- Produces: `HappiBackend.load_metadata()` (happi search → `DeviceInfo`s, stash the `SearchResult` per info for `instantiate`), `instantiate(info)` (`result.get()`), `check_connection` (inherit default). Remove `_start_background_connections` and the `connect()`-conflation; `connect()` becomes metadata availability only (or is removed in favor of `load_metadata`).

- [ ] Steps: failing test (`load_metadata` returns infos for the packaged CMS-style happi json; `instantiate` builds an object via a stubbed `SearchResult.get`) → run-fail → implement → run-pass → commit `feat(devices): HappiBackend via load_metadata/instantiate hooks`.

---

### Task 5: Migrate `MockBackend` to hooks

**Files:**
- Modify: `src/lightfall/devices/backends/mock.py`
- Test: `tests/devices/test_mock_backend.py` (update)

**Interfaces:**
- Produces: `MockBackend.load_metadata()` (return the simulated `DeviceInfo`s), `instantiate(info)` (construct the sim ophyd object — moved out of `connect()`), `check_connection` (return True immediately — sims are always connected).

- [ ] Steps: failing test (`load_metadata` lists the mock devices; `instantiate` returns a sim object; `check_connection` True) → fail → implement → pass → commit `feat(devices): MockBackend via hooks`.

---

### Task 6: Migrate `BCSBackend` to hooks

**Files:**
- Modify: `src/lightfall/devices/backends/bcs.py`
- Test: `tests/devices/test_bcs_backend.py` (update if present)

- [ ] Steps: failing test → fail → implement `load_metadata`/`instantiate`/`check_connection` (mirror current `connect()` load logic, split out instantiation) → pass → commit `feat(devices): BCSBackend via hooks`.

---

### Task 7: Update consumers — plugin loader + CMS endstation

**Files:**
- Modify: `src/lightfall/plugins/loader.py:696-721` (device_backend registration → `DeviceCatalog.add_backend(backend)` instead of `add_and_connect_backend`).
- Note (separate repo): `lightfall-endstation-cms` `plugin.py` already returns `HappiBackend(instantiate="background")`; once core uses the unified path, the `instantiate=` arg is obsolete — the endstation backend just provides the hooks. Track as an endstation follow-up commit.

- [ ] Steps: failing test (loader registers a fake device_backend plugin → catalog `add_backend` called) → fail → implement → pass → commit `refactor(plugins): device_backend plugins load via add_backend`.

---

### Task 8: Remove dead phased-connect code + full-suite green

**Files:**
- Modify: `src/lightfall/devices/connection_manager.py` (remove `connect_all_phased`, `connect_all`, `_do_wait_for_connection` only if fully superseded; keep `connect_device`/`retry_connection` if still used by UI retry).
- Test: full suite.

- [ ] Steps: grep for remaining `connect_all_phased`/`add_and_connect_backend` references; remove dead code; run full suite `PYTHONPATH=src ../../.venv/Scripts/python -m pytest -q`; `ruff check src/`; commit `refactor(devices): drop superseded phased-connect path`.

---

## Box validation (ws5, after merge)

- 101 CMS happi devices: status reaches CONNECTED (not stuck CONNECTING); a few offline IOCs go FAILED/TIMEOUT without blocking the rest; GUI responsive throughout (no main-thread block).
- Confirm the data-browser (tiled) fix still holds (separate, already-merged invoker + adopt).

## Open questions to confirm before/while implementing (from the spec)

- **caproto search batching:** start without it (Task 2 per-device); if box connect-time regresses for 100+ devices, add an optional `backend.prepare(infos)` batch-instantiate hook called once before the concurrent connect.
- **`check_connection` shape:** blocking-in-pool (this plan). Bounded by `max_concurrency`.
- **pool size default:** `max_concurrency=12`; revisit on the box.
