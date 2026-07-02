# Happi Device Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a packaged happi JSON the one way to ship devices with a Lightfall plugin, including code-built ophyd-async device sets, by teaching the device pipeline to connect ophyd-async devices and adding a declarative `HappiDatabasePlugin`.

**Architecture:** Three components, executed B→A→C. **B** adds ophyd-async connection to the existing `instantiate()`→`check_connection()` pipeline by driving `await device.connect(mock=…)` on the BlueskyEngine's continuously-running asyncio loop. **A** adds `HappiDatabasePlugin(DeviceBackendPlugin)` whose `create_backend()` vends a `HappiBackend` over a packaged JSON. **C** converts `lightfall-pystxmcontrol` to a packaged `pystxm_happi.json` and deletes the hand-written `PystxmStxmBackend`.

**Tech Stack:** Python 3.14, PySide/Qt, bluesky 1.14.6, ophyd-async, happi, pytest, loguru.

**Spec:** `docs/superpowers/specs/2026-06-26-happi-device-packaging-design.md`

## Global Constraints

- **Two repos.** Components A & B live in the **lightfall** repo at `C:\Users\rp\PycharmProjects\ncs\lightfall` on branch **`feature/happi-device-packaging`** (already created off `master`). Component C lives in the **lightfall-pystxmcontrol** repo at `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol`.
- **One venv for all tests.** Always use the lightfall venv interpreter (Python 3.14), never bare `pytest`:
  `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest`
  Both packages are editable-installed into it. The editable `lightfall` resolves to the **main** lightfall checkout, so **Component C requires the lightfall checkout to be on `feature/happi-device-packaging`** (A & B present) when its tests run.
- **Git hygiene.** Never `git add -A`; stage explicit paths only. Do **not** touch the unrelated working-tree edit `scripts/diag_live.py` in the lightfall repo. In lightfall-pystxmcontrol, work on a feature branch (current: `feature/stxm-live-map`, which carries the 2026-06-26 `PystxmStxmBackend` migration commit that Task C4 supersedes by deleting the backend).
- **No `ophyd_async` import in lightfall core.** Detect async devices structurally (`inspect.iscoroutinefunction(obj.connect)`), never by importing `ophyd_async`.
- **Commit message footer** (every commit), copied verbatim:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
  ```
- **happi device_class is dotted**, e.g. `lightfall_pystxmcontrol.devices.PystxmAxis` (not `module:Class`).

---

## Component B — ophyd-async connection support (lightfall repo)

### Task B1: Public `event_loop` accessor on BlueskyEngine

**Files:**
- Modify: `C:\Users\rp\PycharmProjects\ncs\lightfall\src\lightfall\acquire\engine\bluesky.py`
- Test: `C:\Users\rp\PycharmProjects\ncs\lightfall\tests\acquire\engine\test_bluesky_event_loop.py`

**Interfaces:**
- Produces: `BlueskyEngine.event_loop -> asyncio.AbstractEventLoop | None` (the engine's loop; `None` before the RunEngine is constructed).

- [ ] **Step 1: Write the failing test**

```python
# tests/acquire/engine/test_bluesky_event_loop.py
"""BlueskyEngine.event_loop exposes the engine's asyncio loop."""
import asyncio

from lightfall.acquire.engine.bluesky import BlueskyEngine


def test_event_loop_is_none_before_start():
    engine = BlueskyEngine()
    assert engine.event_loop is None


def test_event_loop_returns_underlying_loop():
    engine = BlueskyEngine()
    loop = asyncio.new_event_loop()
    try:
        engine._loop = loop  # simulate the loop the RunEngine created
        assert engine.event_loop is loop
    finally:
        loop.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/acquire/engine/test_bluesky_event_loop.py -v`
Expected: FAIL — `AttributeError: 'BlueskyEngine' object has no attribute 'event_loop'`

- [ ] **Step 3: Add the property**

In `bluesky.py`, add this property near the other `@property` definitions (e.g. just after the `state_name` property around line 213). `asyncio` is already imported at module top.

```python
    @property
    def event_loop(self) -> "asyncio.AbstractEventLoop | None":
        """The engine's asyncio event loop.

        bluesky's RunEngine runs this loop continuously on a daemon thread once
        the RunEngine is constructed (in ``_process_queue``). It is the loop the
        RunEngine uses to operate devices, so coroutines scheduled on it via
        ``run_coroutine_threadsafe`` share loop affinity with plan execution.
        Returns ``None`` until the RunEngine has been constructed.
        """
        return self._loop
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/acquire/engine/test_bluesky_event_loop.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall
git add src/lightfall/acquire/engine/bluesky.py tests/acquire/engine/test_bluesky_event_loop.py
git commit -F - <<'EOF'
feat(engine): expose BlueskyEngine.event_loop accessor

Public accessor for the engine's continuously-running asyncio loop, so the
device pipeline can schedule ophyd-async connect() on the same loop the
RunEngine uses (loop affinity).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
EOF
```

---

### Task B2: `connect_async_device` helper

**Files:**
- Create: `C:\Users\rp\PycharmProjects\ncs\lightfall\src\lightfall\devices\async_connect.py`
- Test: `C:\Users\rp\PycharmProjects\ncs\lightfall\tests\devices\test_async_connect.py`

**Interfaces:**
- Consumes: `BlueskyEngine.event_loop` (Task B1); `lightfall.acquire.engine.get_engine`.
- Produces:
  - `is_async_connectable(obj) -> bool`
  - `connect_async_device(obj, *, mock: bool = False, timeout: float = 5.0, loop_wait: float = 5.0) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/devices/test_async_connect.py
"""connect_async_device drives await obj.connect() on the engine loop."""
import asyncio
import threading

import pytest

from lightfall.devices import async_connect


class _AsyncDevice:
    """Minimal ophyd-async-like device: awaitable connect()."""
    def __init__(self):
        self.name = "fake"
        self.connected = False
        self.connect_calls = []

    async def connect(self, mock=False, timeout=10.0, force_reconnect=False):
        self.connect_calls.append(mock)
        self.connected = True


class _ClassicDevice:
    """Classic ophyd-like device: sync connect-ish surface."""
    def __init__(self):
        self.name = "classic"
    def wait_for_connection(self, timeout=None):
        pass


class _RunningLoop:
    """A real asyncio loop running forever on a daemon thread."""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._t = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._t.start()
        while not self.loop.is_running():
            pass

    @property
    def event_loop(self):
        return self.loop

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._t.join(timeout=2.0)
        self.loop.close()


@pytest.fixture
def running_engine(monkeypatch):
    eng = _RunningLoop()
    monkeypatch.setattr(async_connect, "get_engine", lambda: eng)
    yield eng
    eng.close()


def test_is_async_connectable_detects_coroutine_connect():
    assert async_connect.is_async_connectable(_AsyncDevice()) is True
    assert async_connect.is_async_connectable(_ClassicDevice()) is False
    assert async_connect.is_async_connectable(object()) is False


def test_connect_async_device_awaits_connect(running_engine):
    dev = _AsyncDevice()
    ok = async_connect.connect_async_device(dev, mock=False, timeout=2.0)
    assert ok is True
    assert dev.connected is True
    assert dev.connect_calls == [False]


def test_connect_async_device_passes_mock(running_engine):
    dev = _AsyncDevice()
    async_connect.connect_async_device(dev, mock=True, timeout=2.0)
    assert dev.connect_calls == [True]


def test_connect_async_device_no_loop_returns_false(monkeypatch):
    # get_engine returns an engine whose event_loop is None
    monkeypatch.setattr(async_connect, "get_engine",
                        lambda: type("E", (), {"event_loop": None})())
    dev = _AsyncDevice()
    ok = async_connect.connect_async_device(dev, mock=False, timeout=0.5, loop_wait=0.3)
    assert ok is False
    assert dev.connected is False


def test_connect_async_device_connect_raises_returns_false(running_engine):
    class _Boom(_AsyncDevice):
        async def connect(self, mock=False, timeout=10.0, force_reconnect=False):
            raise RuntimeError("nope")
    ok = async_connect.connect_async_device(_Boom(), timeout=2.0)
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/devices/test_async_connect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lightfall.devices.async_connect'`

- [ ] **Step 3: Write the helper**

```python
# src/lightfall/devices/async_connect.py
"""Connect ophyd-async devices on the Bluesky engine's running event loop.

Lightfall's device pipeline (DeviceConnectionManager -> backend.instantiate ->
backend.check_connection) was built for classic (threaded) ophyd, which
connects synchronously via wait_for_connection()/`.connected`. ophyd-async
devices instead require ``await device.connect(...)``. This module drives that
connect on the BlueskyEngine's asyncio loop -- the same loop the RunEngine
later uses to operate the device, so loop affinity is preserved.

Async devices are detected structurally so lightfall core never imports
ophyd_async.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from lightfall.acquire.engine import get_engine
from lightfall.utils.logging import logger


def is_async_connectable(obj: Any) -> bool:
    """True if *obj* exposes an awaitable ``connect`` (ophyd-async style)."""
    return inspect.iscoroutinefunction(getattr(obj, "connect", None))


def _get_engine_loop(loop_wait: float) -> "asyncio.AbstractEventLoop | None":
    """Return the engine's running event loop, waiting up to *loop_wait* seconds."""
    deadline = time.monotonic() + loop_wait
    while True:
        try:
            engine = get_engine()
        except Exception as exc:  # engine not configured yet
            logger.warning("async connect: get_engine() failed: {}", exc)
            engine = None
        loop = getattr(engine, "event_loop", None) if engine is not None else None
        if loop is not None and loop.is_running():
            return loop
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def connect_async_device(
    obj: Any,
    *,
    mock: bool = False,
    timeout: float = 5.0,
    loop_wait: float = 5.0,
) -> bool:
    """Drive ``await obj.connect(mock=mock)`` on the engine loop; return success.

    Returns ``False`` (logging a reason) if no running engine loop becomes
    available within *loop_wait*, or if ``connect()`` raises or exceeds
    *timeout*. Never hangs.
    """
    loop = _get_engine_loop(loop_wait)
    if loop is None:
        logger.error(
            "async connect: no running engine event loop for '{}'; "
            "device left unconnected",
            getattr(obj, "name", obj),
        )
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(obj.connect(mock=mock), loop)
        future.result(timeout=timeout)
        return True
    except Exception as exc:
        logger.warning(
            "async connect: connect() failed for '{}': {}",
            getattr(obj, "name", obj),
            exc,
        )
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/devices/test_async_connect.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall
git add src/lightfall/devices/async_connect.py tests/devices/test_async_connect.py
git commit -F - <<'EOF'
feat(devices): connect_async_device helper for ophyd-async devices

Drives await device.connect(mock=) on the engine's running loop via
run_coroutine_threadsafe, with bounded waits (no hang). Detects async
devices structurally (iscoroutinefunction) so core never imports ophyd_async.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
EOF
```

---

### Task B3: Wire async connect into `DeviceBackend.check_connection`

**Files:**
- Modify: `C:\Users\rp\PycharmProjects\ncs\lightfall\src\lightfall\devices\base.py` (the `check_connection` method, lines 105-119)
- Test: `C:\Users\rp\PycharmProjects\ncs\lightfall\tests\devices\test_base_check_connection.py`

**Interfaces:**
- Consumes: `is_async_connectable`, `connect_async_device` (Task B2), referenced via the `async_connect` module (lazy import — see Step 3).
- Produces: `DeviceBackend.check_connection` now routes async-connectable objects through `connect_async_device`, reading an optional per-backend `self._connect_mock` (default `False`). Classic ophyd path unchanged.

**Why a lazy import:** `base.py` is a foundational early import; `async_connect` imports `get_engine`, which pulls in the bluesky engine. Importing it at `base.py` module top risks a heavy/circular import. So `check_connection` imports the `async_connect` **module** locally and references its functions through it — which is also what the test monkeypatches.

- [ ] **Step 1: Write the failing test**

```python
# tests/devices/test_base_check_connection.py
"""DeviceBackend.check_connection routes ophyd-async devices to the async path."""
from lightfall.devices import async_connect
from lightfall.devices.base import DeviceBackend


class _StubBackend(DeviceBackend):
    """Concrete backend exposing only what check_connection needs."""
    @property
    def name(self): return "stub"
    @property
    def is_connected(self): return True
    def connect(self): return True
    def disconnect(self): return None
    def get_device(self, device_id): return None
    def get_device_by_name(self, name): return None
    def get_device_by_prefix(self, prefix): return None
    def list_devices(self, category=None, beamline=None, active_only=True): return []
    def search_devices(self, query): return []
    def add_device(self, device): return False
    def update_device(self, device): return False
    def remove_device(self, device_id): return False
    def get_device_configurations(self, device_id): return []
    def get_configuration(self, device_id, config_name): return None
    def save_configuration(self, config): return False
    def delete_configuration(self, config_id): return False
    def get_maintenance_history(self, device_id, limit=100): return []
    def add_maintenance_record(self, record): return False


class _AsyncObj:
    name = "a"
    connected = False
    async def connect(self, mock=False): ...


class _ClassicObj:
    def __init__(self): self.connected = True


def test_async_object_routed_to_connect_async_device(monkeypatch):
    calls = {}
    def fake_connect(obj, *, mock, timeout, loop_wait=5.0):
        calls["mock"] = mock
        calls["timeout"] = timeout
        return True
    monkeypatch.setattr(async_connect, "connect_async_device", fake_connect)
    be = _StubBackend()
    assert be.check_connection(_AsyncObj(), timeout=3.0) is True
    assert calls == {"mock": False, "timeout": 3.0}


def test_connect_mock_attribute_forwarded(monkeypatch):
    seen = {}
    monkeypatch.setattr(async_connect, "connect_async_device",
                        lambda obj, *, mock, timeout, loop_wait=5.0: seen.update(mock=mock) or True)
    be = _StubBackend()
    be._connect_mock = True
    be.check_connection(_AsyncObj(), timeout=1.0)
    assert seen["mock"] is True


def test_classic_object_uses_connected_flag(monkeypatch):
    # If the async helper were called this would raise; ensure it is NOT called.
    monkeypatch.setattr(async_connect, "connect_async_device",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    be = _StubBackend()
    assert be.check_connection(_ClassicObj(), timeout=1.0) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/devices/test_base_check_connection.py -v`
Expected: FAIL — `_AsyncObj` falls through `check_connection`'s `connected` branch and (since `connected` stays `False`) returns `False` after polling, so `test_async_object_routed_to_connect_async_device` fails its `is True` assertion (the async device is never connected today).

- [ ] **Step 3: Modify `check_connection` (lazy module import)**

In `base.py`, replace the body of `check_connection` (currently lines ~105-119) with:

```python
    def check_connection(self, obj: Any, timeout: float) -> bool:
        """Block until `obj` is connected or `timeout` elapses; return connected.

        ophyd-async devices require an awaited ``connect()``; for those, drive
        it on the engine loop (see ``connect_async_device``). The optional
        per-backend ``_connect_mock`` attribute (default False) selects
        ``connect(mock=...)``. Classic ophyd uses ``wait_for_connection`` /
        ``connected``; objects with neither are assumed ready.
        """
        # Lazy import: base.py is a foundational early import; async_connect
        # pulls in the engine, so import it only when actually connecting.
        from lightfall.devices import async_connect

        if async_connect.is_async_connectable(obj):
            mock = bool(getattr(self, "_connect_mock", False))
            return async_connect.connect_async_device(obj, mock=mock, timeout=timeout)
        if hasattr(obj, "wait_for_connection"):
            obj.wait_for_connection(timeout=timeout)
            return True
        elif hasattr(obj, "connected"):
            deadline = monotonic() + timeout
            while not obj.connected and monotonic() < deadline:
                sleep(0.05)
            return bool(obj.connected)
        else:
            return True
```

(Note: the async branch is checked **before** `wait_for_connection`/`connected`, because an ophyd-async device may also expose a `connected` attribute. No new module-level import is added to `base.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/devices/test_base_check_connection.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Sanity-check the import path**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -c "import lightfall.devices.base; print('ok')"`
Expected: prints `ok` (the lazy import means importing `base` does not pull in the engine).

- [ ] **Step 6: Run the existing device test suite (no regressions)**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/devices -q`
Expected: PASS (all existing device tests still green).

- [ ] **Step 7: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall
git add src/lightfall/devices/base.py tests/devices/test_base_check_connection.py
git commit -F - <<'EOF'
feat(devices): check_connection connects ophyd-async devices

check_connection now detects async-connectable objects and drives
connect(mock=) on the engine loop via connect_async_device. Universal across
backends (HappiBackend inherits the base method); classic ophyd path
unchanged. Per-backend _connect_mock attribute selects mock connects.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
EOF
```

---

## Component A — `HappiDatabasePlugin` (lightfall repo)

### Task A1: `HappiDatabasePlugin` base class

**Files:**
- Create: `C:\Users\rp\PycharmProjects\ncs\lightfall\src\lightfall\plugins\happi_database_plugin.py`
- Modify: `C:\Users\rp\PycharmProjects\ncs\lightfall\src\lightfall\plugins\__init__.py` (export it)
- Test: `C:\Users\rp\PycharmProjects\ncs\lightfall\tests\plugins\test_happi_database_plugin.py`

**Interfaces:**
- Consumes: `DeviceBackendPlugin` (`name` abstract, `create_backend` abstract, `description` property); `HappiBackend(path, beamline, instantiate)`.
- Produces: `HappiDatabasePlugin(DeviceBackendPlugin)` with class attrs `database_resource: tuple[str,str] | str`, `beamline: str | None = None`, `instantiate: str = "background"`; methods `database_path() -> Path` and `create_backend() -> DeviceBackend`. Subclasses still implement `name`.

- [ ] **Step 1: Write the failing test**

```python
# tests/plugins/test_happi_database_plugin.py
"""HappiDatabasePlugin resolves a packaged/filesystem happi DB and vends a HappiBackend."""
import json

import pytest

from lightfall.devices.backends.happi import HappiBackend
from lightfall.plugins.happi_database_plugin import HappiDatabasePlugin


def _plugin_for_path(path_value, *, beamline=None, instantiate="background"):
    class _P(HappiDatabasePlugin):
        database_resource = path_value
        # class attrs set below per-instance via closures
        @property
        def name(self):
            return "test_happi_plugin"
    _P.beamline = beamline
    _P.instantiate = instantiate
    return _P()


def test_database_path_accepts_filesystem_string(tmp_path):
    db = tmp_path / "devices.json"
    db.write_text(json.dumps({}))
    plugin = _plugin_for_path(str(db))
    assert plugin.database_path() == db


def test_database_path_missing_raises(tmp_path):
    plugin = _plugin_for_path(str(tmp_path / "nope.json"))
    with pytest.raises(FileNotFoundError):
        plugin.database_path()


def test_database_path_resolves_packaged_resource():
    # lightfall ships a package we can resolve a known file from: use the
    # plugins package's own __init__.py as a guaranteed-present resource.
    plugin = _plugin_for_path(("lightfall.plugins", "__init__.py"))
    p = plugin.database_path()
    assert p.exists() and p.name == "__init__.py"


def test_create_backend_returns_configured_happi_backend(tmp_path):
    db = tmp_path / "devices.json"
    db.write_text(json.dumps({}))
    plugin = _plugin_for_path(str(db), beamline="7.0.1", instantiate="background")
    backend = plugin.create_backend()
    assert isinstance(backend, HappiBackend)
    assert backend.name  # has a name
    assert str(db) in str(getattr(backend, "_path"))
    assert getattr(backend, "_beamline") == "7.0.1"


def test_is_device_backend_plugin_subclass():
    from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin
    assert issubclass(HappiDatabasePlugin, DeviceBackendPlugin)
    assert HappiDatabasePlugin.type_name == "device_backend"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/plugins/test_happi_database_plugin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lightfall.plugins.happi_database_plugin'`

- [ ] **Step 3: Write the base class**

```python
# src/lightfall/plugins/happi_database_plugin.py
"""HappiDatabasePlugin: ship devices with a plugin as a packaged happi JSON.

Subclass and declare WHERE the database lives; create_backend() is implemented
here to vend a HappiBackend over it. The JSON ships inside the plugin's wheel
and is resolved via importlib.resources, so it works from an installed package
(not just a source checkout).

Example::

    class MyDevicesPlugin(HappiDatabasePlugin):
        database_resource = ("my_plugin", "devices.json")
        beamline = "7.0.1"

        @property
        def name(self) -> str:
            return "my_devices"
"""
from __future__ import annotations

from abc import abstractmethod
from importlib.resources import files
from pathlib import Path
from typing import ClassVar

from lightfall.devices.backends.happi import HappiBackend
from lightfall.devices.base import DeviceBackend
from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin


class HappiDatabasePlugin(DeviceBackendPlugin):
    """A DeviceBackendPlugin backed by a packaged happi JSON database."""

    #: Either ("package", "resource.json") for a packaged resource, or a
    #: filesystem path string.
    database_resource: ClassVar["tuple[str, str] | str"]
    beamline: ClassVar["str | None"] = None
    instantiate: ClassVar[str] = "background"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique backend-plugin name (used for the enable preference key)."""
        ...

    def database_path(self) -> Path:
        """Resolve ``database_resource`` to a concrete filesystem path.

        - ``("package", "resource.json")`` -> resolved via importlib.resources
          (works from an installed wheel that unpacks to the filesystem).
        - ``str`` -> treated as a filesystem path.

        Raises:
            FileNotFoundError: if the resolved path does not exist.
        """
        res = self.database_resource
        if isinstance(res, tuple):
            package, resource = res
            path = Path(str(files(package).joinpath(resource)))
        else:
            path = Path(res)
        if not path.exists():
            raise FileNotFoundError(
                f"{type(self).__name__}: happi database not found at {path} "
                f"(database_resource={self.database_resource!r})"
            )
        return path

    def create_backend(self) -> DeviceBackend:
        """Vend a HappiBackend over the resolved packaged database."""
        return HappiBackend(
            path=str(self.database_path()),
            beamline=self.beamline,
            instantiate=self.instantiate,
        )
```

- [ ] **Step 4: Export from the plugins package**

In `src/lightfall/plugins/__init__.py`, add `HappiDatabasePlugin` to the imports and `__all__` (mirror how `DeviceBackendPlugin` is exported — find the existing line for it and add a sibling line):

```python
from lightfall.plugins.happi_database_plugin import HappiDatabasePlugin
```
and add `"HappiDatabasePlugin",` to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/plugins/test_happi_database_plugin.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall
git add src/lightfall/plugins/happi_database_plugin.py src/lightfall/plugins/__init__.py tests/plugins/test_happi_database_plugin.py
git commit -F - <<'EOF'
feat(plugins): HappiDatabasePlugin for packaged happi device databases

DeviceBackendPlugin subclass: a plugin declares a packaged happi JSON
(database_resource) + beamline + instantiate mode; create_backend() vends a
HappiBackend over it. importlib.resources path resolution so it works from an
installed wheel.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
EOF
```

---

## Component C — convert pystxm to a packaged happi DB (lightfall-pystxmcontrol repo)

> **Pre-req:** the lightfall checkout must be on `feature/happi-device-packaging` (Tasks B1-A1 committed), because the pystxm tests import `lightfall` from the editable install that points at the main lightfall checkout.

### Task C1: Spike — construct a pystxm device from a happi entry (lock the JSON shape)

**Files:**
- Test: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\tests\test_happi_entry_shape.py`

**Interfaces:**
- Produces: the verified happi-entry kwargs shape for `PystxmAxis`, consumed by Task C2's generator. **Primary** shape: `device_class="lightfall_pystxmcontrol.devices.PystxmAxis"`, `args=[]`, `prefix=""`, `kwargs={"name": "{{name}}", "axis_config": <dict>}`. **Fallback** (if happi mangles the nested dict): a module-level factory, see Step 4.

- [ ] **Step 1: Write the test (primary shape)**

```python
# tests/test_happi_entry_shape.py
"""Verify a pystxm ophyd-async device can be constructed from a happi entry."""
import asyncio
import json

import happi
from happi.backends.json_db import JSONBackend

from lightfall_pystxmcontrol import config
from lightfall_pystxmcontrol.devices import PystxmAxis


def _client(tmp_path):
    db = tmp_path / "one.json"
    db.write_text(json.dumps({}))
    return happi.Client(database=JSONBackend(str(db)))


def test_happi_constructs_pystxm_axis_with_config(tmp_path):
    client = _client(tmp_path)
    client.add_item(happi.OphydItem(
        name="SampleX",
        device_class="lightfall_pystxmcontrol.devices.PystxmAxis",
        args=[],
        prefix="",
        kwargs={"name": "{{name}}", "axis_config": config.DEFAULT_AXES["SampleX"]},
        active=True,
    ))
    result = client.search()[0]
    dev = result.get()
    assert isinstance(dev, PystxmAxis)
    assert dev.name == "SampleX"
    # The config survived as a dict and connect() can build the sim motor.
    asyncio.run(dev.connect(mock=False))
    assert dev._axis_config["axis"] == "X"
```

- [ ] **Step 2: Run it**

Run: `cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/test_happi_entry_shape.py -v`
Expected: **PASS** if happi passes the nested dict through (primary shape confirmed). **If it FAILS** (happi mangled `axis_config`, wrong type, or dropped `name`), go to Step 3 (fallback). Record which shape works — Task C2 uses it.

- [ ] **Step 3 (only if Step 2 failed): add a factory and switch the entry to it**

Append to `src/lightfall_pystxmcontrol/devices.py`:

```python
def make_axis(axis_name: str, name: str = "") -> "PystxmAxis":
    """happi factory: build a PystxmAxis from a named entry in config.DEFAULT_AXES."""
    return PystxmAxis(config.DEFAULT_AXES[axis_name], name=name or axis_name)


def make_counter(name: str = "Counter1", dwell: float = 1.0) -> "PystxmCounter":
    """happi factory: build the simulated counter."""
    return PystxmCounter(config.DEFAULT_COUNTER, dwell=dwell, name=name)
```

And append to `src/lightfall_pystxmcontrol/flyer.py`:

```python
def make_line_flyer(name: str = "STXMLineFlyer") -> "PystxmLineFlyer":
    """happi factory: build the line flyer over the sim counter + X axis."""
    from . import config
    return PystxmLineFlyer(config.DEFAULT_COUNTER, config.DEFAULT_AXES["SampleX"], name=name)
```

Then change the test entry to the factory form and re-run Step 2:

```python
    client.add_item(happi.OphydItem(
        name="SampleX",
        device_class="lightfall_pystxmcontrol.devices.make_axis",
        args=[],
        prefix="",
        kwargs={"name": "{{name}}", "axis_name": "SampleX"},
        active=True,
    ))
```

Expected: PASS. (Factory functions are valid happi `device_class` targets — happi just calls the dotted path with the rendered args/kwargs.)

- [ ] **Step 4: Commit the spike test (and factories if added)**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol
git add tests/test_happi_entry_shape.py
# include devices.py / flyer.py only if Step 3 ran:
# git add src/lightfall_pystxmcontrol/devices.py src/lightfall_pystxmcontrol/flyer.py
git commit -F - <<'EOF'
test: verify pystxm device constructs from a happi entry

Locks the happi-entry kwargs shape used by the packaged device DB.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
EOF
```

---

### Task C2: Generate and commit `pystxm_happi.json`

**Files:**
- Create: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\scripts\build_pystxm_happi_db.py`
- Create (generated): `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\src\lightfall_pystxmcontrol\pystxm_happi.json`
- Modify: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\pyproject.toml` (ensure the JSON is packaged)
- Test: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\tests\test_happi_db.py`

**Interfaces:**
- Consumes: the entry shape locked in C1 (primary or factory).
- Produces: a packaged `pystxm_happi.json` with 4 entries (`SampleX`, `SampleY`, `Counter1`, `STXMLineFlyer`); `HappiBackend(path=that)` loads 4 devices.

- [ ] **Step 1: Write the generator** (uses the **primary** shape; if C1 used the factory fallback, swap `device_class`/`kwargs` accordingly per the comments)

```python
# scripts/build_pystxm_happi_db.py
"""Generate the packaged pystxm_happi.json from config.DEFAULT_* dicts.

Run after changing config.py; commit the resulting JSON.
    <lightfall-venv>/python scripts/build_pystxm_happi_db.py
"""
import json
from pathlib import Path

import happi
from happi.backends.json_db import JSONBackend

from lightfall_pystxmcontrol import config

OUT = (Path(__file__).resolve().parents[1]
       / "src" / "lightfall_pystxmcontrol" / "pystxm_happi.json")


def build() -> None:
    OUT.write_text(json.dumps({}))
    client = happi.Client(database=JSONBackend(str(OUT)))

    for axis_name, axis_cfg in config.DEFAULT_AXES.items():
        client.add_item(happi.OphydItem(
            name=axis_name,
            device_class="lightfall_pystxmcontrol.devices.PystxmAxis",
            args=[], prefix="",
            kwargs={"name": "{{name}}", "axis_config": axis_cfg},
            # FACTORY FALLBACK (if C1 used it):
            # device_class="lightfall_pystxmcontrol.devices.make_axis",
            # kwargs={"name": "{{name}}", "axis_name": axis_name},
            active=True,
        ))

    client.add_item(happi.OphydItem(
        name="Counter1",
        device_class="lightfall_pystxmcontrol.devices.PystxmCounter",
        args=[], prefix="",
        kwargs={"name": "{{name}}", "daq_config": config.DEFAULT_COUNTER, "dwell": 1.0},
        # FACTORY FALLBACK:
        # device_class="lightfall_pystxmcontrol.devices.make_counter",
        # kwargs={"name": "{{name}}", "dwell": 1.0},
        active=True,
    ))

    client.add_item(happi.OphydItem(
        name="STXMLineFlyer",
        device_class="lightfall_pystxmcontrol.flyer.PystxmLineFlyer",
        args=[], prefix="",
        kwargs={"name": "{{name}}", "daq_config": config.DEFAULT_COUNTER,
                "x_axis_config": config.DEFAULT_AXES["SampleX"]},
        # FACTORY FALLBACK:
        # device_class="lightfall_pystxmcontrol.flyer.make_line_flyer",
        # kwargs={"name": "{{name}}"},
        active=True,
    ))

    print(f"Wrote {OUT} ({len(client.search())} devices)")


if __name__ == "__main__":
    build()
```

- [ ] **Step 2: Run the generator**

Run: `cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python scripts/build_pystxm_happi_db.py`
Expected: prints `Wrote …pystxm_happi.json (4 devices)` and creates the JSON.

- [ ] **Step 3: Write the DB load test**

```python
# tests/test_happi_db.py
"""The packaged pystxm_happi.json loads 4 devices through HappiBackend."""
from importlib.resources import files

from lightfall.devices.backends.happi import HappiBackend


def _db_path():
    return str(files("lightfall_pystxmcontrol").joinpath("pystxm_happi.json"))


def test_backend_loads_four_devices():
    be = HappiBackend(path=_db_path(), instantiate="background")
    be.connect()
    names = {d.name for d in be.list_devices(active_only=False)}
    assert names == {"SampleX", "SampleY", "Counter1", "STXMLineFlyer"}


def test_instantiate_builds_expected_classes():
    from lightfall_pystxmcontrol.devices import PystxmAxis, PystxmCounter
    from lightfall_pystxmcontrol.flyer import PystxmLineFlyer
    be = HappiBackend(path=_db_path(), instantiate="background")
    be.connect()
    by_name = {d.name: d for d in be.list_devices(active_only=False)}
    assert isinstance(be.instantiate(by_name["SampleX"]), PystxmAxis)
    assert isinstance(be.instantiate(by_name["Counter1"]), PystxmCounter)
    assert isinstance(be.instantiate(by_name["STXMLineFlyer"]), PystxmLineFlyer)
```

- [ ] **Step 4: Run the DB load test**

Run: `cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/test_happi_db.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Ensure the JSON ships in the wheel**

Open `pyproject.toml`. Hatchling includes files under the package directory by default; confirm there is no `exclude` that drops `*.json`, and that the wheel target includes the package. If a `[tool.hatch.build.targets.wheel]` section restricts `include`, add the resource. Verify packaging picks it up:

Run: `cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -c "from importlib.resources import files; p=files('lightfall_pystxmcontrol').joinpath('pystxm_happi.json'); print(p, p.is_file())"`
Expected: prints the path and `True`.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol
git add scripts/build_pystxm_happi_db.py src/lightfall_pystxmcontrol/pystxm_happi.json tests/test_happi_db.py pyproject.toml
git commit -F - <<'EOF'
feat: packaged pystxm_happi.json device database + generator

Ships the 4 simulated STXM devices (SampleX, SampleY, Counter1,
STXMLineFlyer) as a happi JSON read by the built-in HappiBackend, replacing
the hand-written backend. Generator script rebuilds it from config.py.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
EOF
```

---

### Task C3: Convert `PystxmBackendPlugin` to `HappiDatabasePlugin`

**Files:**
- Modify: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\src\lightfall_pystxmcontrol\plugin.py`
- Test: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\tests\test_plugin_backend.py`

**Interfaces:**
- Consumes: `HappiDatabasePlugin` (Task A1); packaged `pystxm_happi.json` (Task C2).
- Produces: `PystxmBackendPlugin` whose `create_backend()` returns a `HappiBackend` over the packaged DB. `name` stays `"pystxmcontrol"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plugin_backend.py
"""PystxmBackendPlugin vends a HappiBackend over the packaged device DB."""
from lightfall.devices.backends.happi import HappiBackend
from lightfall.plugins.happi_database_plugin import HappiDatabasePlugin

from lightfall_pystxmcontrol.plugin import PystxmBackendPlugin


def test_plugin_is_happi_database_plugin():
    assert issubclass(PystxmBackendPlugin, HappiDatabasePlugin)
    assert PystxmBackendPlugin().name == "pystxmcontrol"


def test_create_backend_points_at_packaged_db():
    plugin = PystxmBackendPlugin()
    assert plugin.database_path().name == "pystxm_happi.json"
    backend = plugin.create_backend()
    assert isinstance(backend, HappiBackend)
    backend.connect()
    names = {d.name for d in backend.list_devices(active_only=False)}
    assert names == {"SampleX", "SampleY", "Counter1", "STXMLineFlyer"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/test_plugin_backend.py -v`
Expected: FAIL — `PystxmBackendPlugin` is still the old `DeviceBackendPlugin` returning `PystxmStxmBackend`, so `issubclass(... HappiDatabasePlugin)` is False.

- [ ] **Step 3: Rewrite `plugin.py`**

```python
# src/lightfall_pystxmcontrol/plugin.py
"""Lightfall HappiDatabasePlugin for pystxmcontrol simulated STXM devices."""
from lightfall.plugins.happi_database_plugin import HappiDatabasePlugin


class PystxmBackendPlugin(HappiDatabasePlugin):
    """Exposes simulated pystxmcontrol STXM devices from a packaged happi DB.

    Registered as a ``device_backend`` plugin under the ``lightfall.plugins``
    entry-point group. The device set ships as ``pystxm_happi.json`` inside this
    package and is loaded by Lightfall's built-in HappiBackend.
    """

    database_resource = ("lightfall_pystxmcontrol", "pystxm_happi.json")
    instantiate = "background"

    @property
    def name(self) -> str:
        return "pystxmcontrol"

    @property
    def description(self) -> str:
        return "Simulated pystxmcontrol STXM devices (motors + counter)"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/test_plugin_backend.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol
git add src/lightfall_pystxmcontrol/plugin.py tests/test_plugin_backend.py
git commit -F - <<'EOF'
refactor: PystxmBackendPlugin is now a HappiDatabasePlugin

create_backend() vends a HappiBackend over the packaged pystxm_happi.json
instead of the hand-written PystxmStxmBackend.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
EOF
```

---

### Task C4: Retire `PystxmStxmBackend` and prove end-to-end connect

**Files:**
- Delete: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\src\lightfall_pystxmcontrol\backend.py`
- Delete: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\tests\test_backend.py` (the 2026-06-26 migration tests; superseded by `test_happi_db.py` + `test_plugin_backend.py`)
- Test: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\tests\test_happi_async_connect.py`

**Interfaces:**
- Consumes: Component B (`connect_async_device`), packaged DB (C2).
- Produces: proof that a happi-built pystxm device connects via the pipeline path and is functional.

- [ ] **Step 1: Confirm nothing imports the old backend**

Run: `cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && grep -rn "PystxmStxmBackend\|from .backend\|from lightfall_pystxmcontrol.backend\|import backend" src tests`
Expected: **no matches** (Tasks C2/C3 removed the references). If any remain, update them to use the happi path before deleting.

- [ ] **Step 2: Write the end-to-end async-connect test**

This reuses a real running loop (as Component B does) to drive the pipeline's `instantiate()` + `check_connection()` exactly as `DeviceConnectionManager` would, proving async connect works for a happi-built device.

```python
# tests/test_happi_async_connect.py
"""A happi-built pystxm device connects via check_connection (async path)."""
import asyncio
import threading
from importlib.resources import files

import pytest

from lightfall.devices import async_connect
from lightfall.devices.backends.happi import HappiBackend


class _RunningLoop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._t = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._t.start()
        while not self.loop.is_running():
            pass
    @property
    def event_loop(self):
        return self.loop
    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._t.join(timeout=2.0)
        self.loop.close()


@pytest.fixture
def running_engine(monkeypatch):
    eng = _RunningLoop()
    monkeypatch.setattr(async_connect, "get_engine", lambda: eng)
    yield eng
    eng.close()


def _backend():
    db = str(files("lightfall_pystxmcontrol").joinpath("pystxm_happi.json"))
    be = HappiBackend(path=db, instantiate="background")
    be.connect()
    return be


def test_axis_connects_and_reads_via_pipeline(running_engine):
    be = _backend()
    info = next(d for d in be.list_devices(active_only=False) if d.name == "SampleX")
    obj = be.instantiate(info)              # constructs (not connected)
    assert obj.connected is False
    ok = be.check_connection(obj, timeout=5.0)  # async path drives connect()
    assert ok is True
    assert obj.connected is True
    # functional: readback reads a real value from the sim motor
    reading = asyncio.run_coroutine_threadsafe(obj.read(), running_engine.loop).result(5.0)
    assert "SampleX" in reading
```

- [ ] **Step 3: Run it**

Run: `cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/test_happi_async_connect.py -v`
Expected: PASS (1 passed). (If `read()` keying differs, assert on the device being connected and `obj._motor is not None` instead — the connect is the thing under test.)

- [ ] **Step 4: Delete the old backend and its tests**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol
git rm src/lightfall_pystxmcontrol/backend.py tests/test_backend.py
```

- [ ] **Step 5: Run the full plugin test suite**

Run: `cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest tests/ -q`
Expected: PASS (all green; the deleted backend tests are gone, replaced by the happi tests).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol
git add tests/test_happi_async_connect.py
git commit -F - <<'EOF'
feat: retire hand-written PystxmStxmBackend; devices load via happi

Deletes backend.py (the ~310-line DeviceBackend re-implementation that drifted
out of sync with the unified load pipeline and crashed startup) and its
migration tests. Devices now load from pystxm_happi.json and connect via the
new ophyd-async pipeline path. Adds an end-to-end connect test.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
EOF
```

---

### Task C5: Docs + final verification

**Files:**
- Modify: `C:\Users\rp\PycharmProjects\ncs\lightfall-pystxmcontrol\README.md` (device section)

- [ ] **Step 1: Update the README**

In the README's device/architecture section, replace any description of a custom `PystxmStxmBackend` with: devices are defined in `src/lightfall_pystxmcontrol/pystxm_happi.json` (regenerate via `scripts/build_pystxm_happi_db.py`) and loaded by Lightfall's built-in `HappiBackend` through the `HappiDatabasePlugin` base class. Note that the device classes (`PystxmAxis`, `PystxmCounter`, `PystxmLineFlyer`) are ophyd-async and are connected by Lightfall's device pipeline.

- [ ] **Step 2: Full suites, both repos**

Run:
```
C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest C:/Users/rp/PycharmProjects/ncs/lightfall/tests/devices C:/Users/rp/PycharmProjects/ncs/lightfall/tests/plugins C:/Users/rp/PycharmProjects/ncs/lightfall/tests/acquire/engine -q
C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest C:/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol/tests -q
```
Expected: both PASS.

- [ ] **Step 3: Verify the original crash is gone (the startup symptom)**

Drive the exact catalog hook sequence against the new happi-backed plugin (no `NotImplementedError`, devices load):

Run:
```
cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol && C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -c "
from lightfall_pystxmcontrol.plugin import PystxmBackendPlugin
be = PystxmBackendPlugin().create_backend()
be.connect()
infos = be.load_metadata()
print('load_metadata ->', sorted(i.name for i in infos))
print('OK - happi backend, no hand-written DeviceBackend')
"
```
Expected: prints the 4 device names and `OK …` with no traceback.

- [ ] **Step 4: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/lightfall-pystxmcontrol
git add README.md
git commit -F - <<'EOF'
docs: describe happi-packaged device database

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01E5rHNPQgibARJ7jgUjzCpo
EOF
```

---

## Final State

- **lightfall** (`feature/happi-device-packaging`): `BlueskyEngine.event_loop`, `devices/async_connect.py`, async-aware `check_connection`, `plugins/happi_database_plugin.py`. The device pipeline now connects ophyd-async devices on the engine loop.
- **lightfall-pystxmcontrol**: devices ship as `pystxm_happi.json`; `PystxmBackendPlugin` is a `HappiDatabasePlugin`; `PystxmStxmBackend` deleted. No hand-written `DeviceBackend` to drift again.
- Any future plugin ships devices by packaging a happi JSON and subclassing `HappiDatabasePlugin` — the one canonical path.
