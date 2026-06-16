"""Tests for late backend addition to DeviceCatalog."""
from __future__ import annotations

from uuid import uuid4

from lightfall.devices.base import DeviceBackend
from lightfall.devices.catalog import DeviceCatalog
from lightfall.devices.model import DeviceCategory, DeviceInfo


class _FakeBackend(DeviceBackend):
    """Minimal backend exposing two devices once 'connected'."""

    def __init__(self, name="fake", ok=True):
        self._name = name
        self._ok = ok
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
    def connect(self): self._connected = self._ok; return self._ok
    def disconnect(self): self._connected = False
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


def test_add_and_connect_backend_registers_backend_synchronously(monkeypatch):
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
    assert started["count"] == 1           # worker was launched


def test_finish_backend_connect_merges_devices_and_emits_signal():
    catalog = DeviceCatalog()
    backend = _FakeBackend()
    backend.connect()
    catalog.add_backend(backend)  # register so get_all_devices can reach it
    seen = []
    catalog.backend_connected.connect(seen.append)
    catalog._finish_backend_connect(backend, True)
    names = {d.name for d in catalog.get_all_devices()}
    assert {"fake_dev1", "fake_dev2"} <= names
    assert seen == ["fake"]


def test_finish_backend_connect_failed_does_not_merge():
    catalog = DeviceCatalog()
    backend = _FakeBackend(ok=False)
    backend.connect()              # is_connected True so list_devices is reachable
    catalog.add_backend(backend)   # register so the cache/merge path is reachable

    seen = []
    catalog.backend_connected.connect(seen.append)

    catalog._finish_backend_connect(backend, False)

    assert all(d.name not in {"fake_dev1", "fake_dev2"} for d in catalog.get_all_devices())
    assert seen == [], f"backend_connected emitted unexpectedly: {seen}"


def test_on_backend_connect_error_treats_as_failed():
    catalog = DeviceCatalog()
    backend = _FakeBackend()
    catalog.add_backend(backend)

    seen = []
    catalog.backend_connected.connect(seen.append)

    catalog._on_backend_connect_error(backend, RuntimeError("boom"))

    assert seen == []
    assert all(d.name not in {"fake_dev1", "fake_dev2"} for d in catalog.get_all_devices())
