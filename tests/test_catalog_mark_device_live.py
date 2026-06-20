"""DeviceCatalog.mark_device_live: attach a live ophyd instance + refresh state."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from lightfall.devices.catalog import DeviceCatalog
from lightfall.devices.model import DeviceCategory, DeviceInfo, DeviceStatus


@pytest.fixture
def catalog():
    DeviceCatalog.reset_instance()
    cat = DeviceCatalog.get_instance()
    info = DeviceInfo(id=uuid4(), name="smx", category=DeviceCategory.MOTOR)
    cat._device_cache[info.id] = info  # seed the cache directly for the unit test
    cat._name_index[info.name] = info.id
    yield cat, info
    DeviceCatalog.reset_instance()


def test_mark_device_live_connected_sets_online_and_emits(catalog):
    cat, info = catalog
    states: list = []
    connected: list = []
    cat.device_state_changed.connect(lambda did, st: states.append((did, st)))
    cat.device_connected.connect(lambda did: connected.append(did))

    obj = SimpleNamespace(connected=True)
    assert cat.mark_device_live(info.id, obj) is True

    assert info._ophyd_device is obj
    assert info._state.status == DeviceStatus.ONLINE
    assert info._state.connected is True
    assert states and states[-1][0] == str(info.id)
    assert connected == [str(info.id)]


def test_mark_device_live_probes_connected_flag(catalog):
    cat, info = catalog
    # A freshly instantiated device whose CA channels haven't connected yet.
    obj = SimpleNamespace(connected=False)
    cat.mark_device_live(info.id, obj)
    assert info._state.status == DeviceStatus.CONNECTING
    assert info._state.connected is False


def test_mark_device_live_explicit_connected_overrides_probe(catalog):
    cat, info = catalog
    obj = SimpleNamespace(connected=False)
    cat.mark_device_live(info.id, obj, connected=True)
    assert info._state.status == DeviceStatus.ONLINE


def test_mark_device_live_unknown_device_returns_false(catalog):
    cat, _info = catalog
    assert cat.mark_device_live(uuid4(), SimpleNamespace(connected=True)) is False


def test_mark_device_live_does_not_touch_backend(catalog, monkeypatch):
    """Regression: must NOT write through to the backend (no happi JSON rewrite)."""
    cat, info = catalog
    calls: list = []

    # Spy on the catalog's backend-resolution path: mark_device_live must never
    # reach a backend (which is what update_device() does and would rewrite the
    # happi JSON). If it consulted one, this would record a call.
    monkeypatch.setattr(
        cat, "_backend_for_device", lambda did: calls.append(did) or None
    )

    assert cat.mark_device_live(info.id, SimpleNamespace(connected=True)) is True
    assert calls == []
