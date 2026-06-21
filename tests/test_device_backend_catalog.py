"""Tests for late backend addition to DeviceCatalog."""
from __future__ import annotations

from uuid import uuid4

import pytest

from lightfall.devices.base import DeviceBackend
from lightfall.devices.catalog import DeviceCatalog
from lightfall.devices.connection_manager import DeviceConnectionManager
from lightfall.devices.model import DeviceCategory, DeviceInfo


class _FakeBackend(DeviceBackend):
    """Minimal backend exposing two devices once 'connected'.

    Implements load_metadata() for the unified pipeline.
    """

    def __init__(self, name="fake", ok=True, load_raises=False):
        self._name = name
        self._ok = ok
        self._load_raises = load_raises
        self._infos = [
            DeviceInfo(id=uuid4(), name=f"{name}_dev1", category=DeviceCategory.MOTOR),
            DeviceInfo(id=uuid4(), name=f"{name}_dev2", category=DeviceCategory.MOTOR),
        ]
        self._connected = False

    @property
    def name(self): return self._name
    @property
    def is_connected(self): return self._connected
    @property
    def is_editable(self): return False
    def connect(self):
        self._connected = self._ok
        return self._ok
    def disconnect(self): self._connected = False

    def load_metadata(self):
        if self._load_raises:
            raise RuntimeError("intentional load failure")
        return list(self._infos)

    def instantiate(self, info):
        return info

    def check_connection(self, obj, timeout):
        return True

    def get_device(self, device_id): return next((d for d in self._infos if d.id == device_id), None)
    def get_device_by_name(self, name): return next((d for d in self._infos if d.name == name), None)
    def get_device_by_prefix(self, prefix): return None
    def list_devices(self, category=None, beamline=None, active_only=True): return list(self._infos)
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


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Fresh singletons before/after each test."""
    DeviceCatalog.reset_instance()
    DeviceConnectionManager.reset_instance()
    yield
    DeviceConnectionManager.reset_instance()
    DeviceCatalog.reset_instance()


def test_add_and_connect_backend_registers_backend_synchronously(monkeypatch):
    """Backend is registered synchronously; a worker is launched immediately."""
    started = {"count": 0}

    class _FakeFuture:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            started["count"] += 1

    import lightfall.devices.catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, "QThreadFuture", _FakeFuture)

    catalog = DeviceCatalog()
    backend = _FakeBackend()
    catalog.add_and_connect_backend(backend)

    assert "fake" in catalog.backends      # registered synchronously, before connect
    assert started["count"] == 1           # load+connect worker was launched


def test_load_and_connect_backend_merges_devices_and_emits_signal(qtbot):
    """_load_and_connect_backend merges devices into the cache and emits signals.

    Replaces the old direct _finish_backend_connect(backend, True) call.
    """
    catalog = DeviceCatalog.get_instance()
    backend = _FakeBackend()

    backend_seen = []
    added = []
    catalog.backend_connected.connect(backend_seen.append)
    catalog.device_added.connect(added.append)

    catalog.add_and_connect_backend(backend)

    # Wait for both backend_connected and device_added for both devices
    qtbot.waitUntil(lambda: backend_seen == ["fake"], timeout=3000)
    qtbot.waitUntil(
        lambda: {d.name for d in added} == {"fake_dev1", "fake_dev2"},
        timeout=3000,
    )

    # Devices are in the cache
    assert catalog.get_device_by_name("fake_dev1") is not None
    assert catalog.get_device_by_name("fake_dev2") is not None


def test_load_metadata_raising_suppresses_backend_connected(qtbot):
    """When load_metadata() raises, backend_connected must NOT fire.

    Pre-refactor contract: a backend whose load failed does not signal connected.
    No devices must be placed in the catalog's _device_cache either.
    """
    import time

    from PySide6.QtWidgets import QApplication

    catalog = DeviceCatalog.get_instance()
    backend = _FakeBackend(load_raises=True)

    backend_seen = []
    added = []
    catalog.backend_connected.connect(backend_seen.append)
    catalog.device_added.connect(added.append)

    catalog.add_and_connect_backend(backend)

    # Wait for the worker thread to finish and _on_error to run on main thread.
    deadline = 2.0
    step = 0.1
    elapsed = 0.0
    app = QApplication.instance()
    while elapsed < deadline:
        time.sleep(step)
        elapsed += step
        if app:
            app.processEvents()

    # backend_connected must NOT have fired
    assert backend_seen == [], (
        f"backend_connected must NOT fire on load failure, got: {backend_seen}"
    )

    # No devices contributed via the pipeline (cache is empty)
    assert added == []
    assert len(catalog._device_cache) == 0
    assert len(catalog._name_index) == 0


def test_unexpected_worker_error_suppresses_backend_connected(qtbot):
    """When the load worker raises unexpectedly, backend_connected must NOT fire.

    Any exception from load_metadata() routes through _on_error which must
    suppress the signal and register no devices.
    """
    import time

    from PySide6.QtWidgets import QApplication

    catalog = DeviceCatalog.get_instance()

    class _BombBackend(_FakeBackend):
        """Backend whose load_metadata raises an unexpected error."""
        def load_metadata(self):
            raise RuntimeError("unexpected boom")

    backend = _BombBackend(name="bomber")

    backend_seen = []
    added = []
    catalog.backend_connected.connect(backend_seen.append)
    catalog.device_added.connect(added.append)

    catalog.add_and_connect_backend(backend)

    # Wait for worker + _on_error to run on the main thread.
    deadline = 2.0
    step = 0.1
    elapsed = 0.0
    app = QApplication.instance()
    while elapsed < deadline:
        time.sleep(step)
        elapsed += step
        if app:
            app.processEvents()

    assert backend_seen == [], (
        f"backend_connected must NOT fire when load_metadata() raises, got: {backend_seen}"
    )
    assert added == []
