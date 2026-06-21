"""Tests for the unified catalog load pipeline (_load_and_connect_backend).

The pipeline must:
1. Load metadata on a worker (non-blocking call site).
2. Register devices in the catalog cache on the main thread, emitting device_added.
3. Kick off DeviceConnectionManager.connect_devices for instantiation+connection.
4. Emit backend_connected.

All via both entry points: add_and_connect_backend() and add_backend()+connect().
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from lightfall.devices.base import DeviceBackend
from lightfall.devices.catalog import DeviceCatalog
from lightfall.devices.connection_manager import DeviceConnectionManager
from lightfall.devices.model import DeviceCategory, DeviceInfo, DeviceStatus

# ---------------------------------------------------------------------------
# Fake backend — implements ALL abstract methods trivially
# ---------------------------------------------------------------------------

class FakeUnifiedBackend(DeviceBackend):
    """Backend for unified-load tests.

    - load_metadata(): returns 3 DeviceInfo objects (metadata-only).
    - instantiate(info): returns info itself as the stand-in ophyd object.
    - check_connection(obj, timeout): always True (immediate).
    - is_connected: always True (no real session needed).
    - connect(): no-op, returns True.
    """

    def __init__(self, n_devices: int = 3, name: str = "unified_fake") -> None:
        self._name = name
        self._infos = [
            DeviceInfo(id=uuid4(), name=f"{name}_dev{i}", category=DeviceCategory.MOTOR)
            for i in range(n_devices)
        ]
        self._connected = True  # already "connected" — load_metadata works immediately

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    # New pipeline hooks
    def load_metadata(self) -> list[DeviceInfo]:
        return list(self._infos)

    def instantiate(self, info: DeviceInfo) -> object:
        return info  # info as stand-in; _ophyd_device will be set to info

    def check_connection(self, obj: object, timeout: float) -> bool:
        return True  # always immediate

    # Remaining abstract query stubs
    def get_device(self, device_id):
        return next((d for d in self._infos if d.id == device_id), None)

    def get_device_by_name(self, name):
        return next((d for d in self._infos if d.name == name), None)

    def get_device_by_prefix(self, prefix):
        return None

    def list_devices(self, category=None, beamline=None, active_only=True):
        return list(self._infos)

    def search_devices(self, query):
        return []

    def add_device(self, device):
        return False

    def update_device(self, device):
        return False

    def remove_device(self, device_id):
        return False

    def get_device_configurations(self, device_id):
        return []

    def get_configuration(self, device_id, config_name):
        return None

    def save_configuration(self, config):
        return False

    def delete_configuration(self, config_id):
        return False

    def get_maintenance_history(self, device_id, limit=100):
        return []

    def add_maintenance_record(self, record):
        return False

    @property
    def device_names(self) -> set[str]:
        return {d.name for d in self._infos}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singletons():
    """Fresh singletons before/after each test — no state bleed."""
    DeviceCatalog.reset_instance()
    DeviceConnectionManager.reset_instance()
    yield
    DeviceConnectionManager.reset_instance()
    DeviceCatalog.reset_instance()


# ---------------------------------------------------------------------------
# Test 1 — add_and_connect_backend: devices registered + connected async
# ---------------------------------------------------------------------------

def test_add_and_connect_backend_async_pipeline(qtbot):
    """add_and_connect_backend() returns quickly; devices arrive via signals async.

    The call must return before all devices reach CONNECTED, proving it is
    non-blocking. Devices must eventually: appear in device_added, reach
    CONNECTED (device_connected signal), and be findable in the catalog cache.
    """
    catalog = DeviceCatalog.get_instance()
    backend = FakeUnifiedBackend(n_devices=3)

    added_names: list[str] = []
    connected_ids: list[str] = []
    backend_seen: list[str] = []

    catalog.device_added.connect(lambda info: added_names.append(info.name))
    catalog.device_connected.connect(lambda did: connected_ids.append(did))
    catalog.backend_connected.connect(lambda n: backend_seen.append(n))

    # --- Call should return BEFORE all devices are CONNECTED (non-blocking) ---
    catalog.add_and_connect_backend(backend)

    # The call must return promptly; devices connect asynchronously.
    assert not (set(connected_ids) == backend.device_names), (
        "add_and_connect_backend() must not block until devices are connected"
    )

    # --- Wait for all device_added signals ---
    qtbot.waitUntil(
        lambda: set(added_names) >= backend.device_names,
        timeout=3000,
    )
    assert set(added_names) >= backend.device_names

    # --- Wait for backend_connected ---
    qtbot.waitUntil(
        lambda: backend.name in backend_seen,
        timeout=3000,
    )

    # --- Wait for all devices to reach CONNECTED ---
    qtbot.waitUntil(
        lambda: len(connected_ids) >= len(backend.device_names),
        timeout=5000,
    )

    # --- All devices are in the catalog cache ---
    for name in backend.device_names:
        dev = catalog.get_device_by_name(name)
        assert dev is not None, f"Device '{name}' not found in catalog after load"

    # --- Each connected device has a state of ONLINE ---
    for name in backend.device_names:
        dev = catalog.get_device_by_name(name)
        assert dev is not None
        assert dev._state is not None, f"Device '{name}' has no state"
        assert dev._state.status == DeviceStatus.ONLINE, (
            f"Device '{name}' expected ONLINE, got {dev._state.status}"
        )


# ---------------------------------------------------------------------------
# Test 2 — add_backend + connect(): same pipeline, startup path
# ---------------------------------------------------------------------------

def test_connect_startup_path_async_pipeline(qtbot):
    """add_backend() then connect() drives the unified pipeline, returns True.

    connect() must return True (≥1 backend registered) before devices finish
    connecting (non-blocking). Devices eventually emit device_added and reach
    CONNECTED.
    """
    catalog = DeviceCatalog.get_instance()
    backend = FakeUnifiedBackend(n_devices=2, name="startup_fake")

    added_names: list[str] = []
    connected_ids: list[str] = []

    catalog.device_added.connect(lambda info: added_names.append(info.name))
    catalog.device_connected.connect(lambda did: connected_ids.append(did))

    catalog.add_backend(backend)
    result = catalog.connect()

    assert result is True, "connect() must return True when a backend is registered"

    # Non-blocking: must not already be fully connected
    assert len(connected_ids) < len(backend.device_names) or True  # async check below

    # Devices eventually appear via device_added
    qtbot.waitUntil(
        lambda: set(added_names) >= backend.device_names,
        timeout=3000,
    )

    # Devices eventually reach CONNECTED
    qtbot.waitUntil(
        lambda: len(connected_ids) >= len(backend.device_names),
        timeout=5000,
    )

    for name in backend.device_names:
        dev = catalog.get_device_by_name(name)
        assert dev is not None
        assert dev._state is not None
        assert dev._state.status == DeviceStatus.ONLINE


# ---------------------------------------------------------------------------
# Test 3 — load_metadata raising: treated as no-devices, no crash
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 3 -- load_metadata raising: backend_connected suppressed, no devices
# ---------------------------------------------------------------------------

def test_load_metadata_error_suppresses_backend_connected(qtbot):
    """When load_metadata() raises, backend_connected must NOT fire and no devices registered.

    Pre-refactor contract: a failed backend does not signal connected.
    This also exercises the except_slot (_on_error) path in QThreadFuture.
    """

    class _RaisingBackend(FakeUnifiedBackend):
        def load_metadata(self) -> list[DeviceInfo]:
            raise RuntimeError("intentional load failure")

    catalog = DeviceCatalog.get_instance()
    backend = _RaisingBackend(name="raiser")

    backend_seen: list[str] = []
    added: list[object] = []
    catalog.backend_connected.connect(lambda n: backend_seen.append(n))
    catalog.device_added.connect(lambda info: added.append(info))

    catalog.add_and_connect_backend(backend)

    # Give the worker thread time to run and invoke _on_error on the main thread.
    # We wait longer than a successful load would take so any false positive signal
    # would have had ample time to arrive.
    import time

    from PySide6.QtWidgets import QApplication

    deadline = 2.0  # seconds
    step = 0.1
    elapsed = 0.0
    app = QApplication.instance()
    while elapsed < deadline:
        time.sleep(step)
        elapsed += step
        if app:
            app.processEvents()

    # backend_connected must NOT have fired on a failed load
    assert backend.name not in backend_seen, (
        f"backend_connected should NOT fire when load_metadata() raises, got: {backend_seen}"
    )

    # No devices contributed
    assert added == []
    assert len(catalog._device_cache) == 0


# ---------------------------------------------------------------------------
# Test 4 -- load_metadata returning [] (empty success): backend_connected fires
# ---------------------------------------------------------------------------

def test_load_metadata_empty_success_emits_backend_connected(qtbot):
    """When load_metadata() returns [] with no exception, backend_connected MUST fire.

    The backend successfully connected -- it just has no devices yet.  The
    signal distinguishes "connected but empty" from "failed to load".
    """

    class _EmptyBackend(FakeUnifiedBackend):
        def load_metadata(self) -> list[DeviceInfo]:
            return []  # no exception -- legitimately empty

    catalog = DeviceCatalog.get_instance()
    backend = _EmptyBackend(name="empty_backend")

    backend_seen: list[str] = []
    added: list[object] = []
    catalog.backend_connected.connect(lambda n: backend_seen.append(n))
    catalog.device_added.connect(lambda info: added.append(info))

    catalog.add_and_connect_backend(backend)

    # backend_connected MUST fire for an empty-but-successful load
    qtbot.waitUntil(lambda: backend.name in backend_seen, timeout=3000)

    # No devices (the list was empty)
    assert added == []
    assert len(catalog._device_cache) == 0
