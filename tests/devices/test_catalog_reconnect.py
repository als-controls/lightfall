"""Failed device connections surface as OFFLINE/ERROR and can be retried per device.

Regression for devices stuck at CONNECTING: ``_on_device_connecting`` marks a
device that already has a state (i.e. every attempt after the first) as
CONNECTING, and ``_on_device_failed`` used to skip any device already in
CONNECTING, so a failed retry was never recorded. Retry goes
through the owning backend's ``instantiate`` (the same path as the initial
load), so it works for CSM/happi/BCS/mock devices alike, not only for devices
that carry a stashed happi result.
"""

from __future__ import annotations

import pytest

from lightfall.devices.base import DeviceBackend
from lightfall.devices.catalog import DeviceCatalog
from lightfall.devices.connection_manager import DeviceConnectionManager
from lightfall.devices.model import DeviceState, DeviceStatus
from tests.devices.test_catalog_unified_load import FakeUnifiedBackend


class FlakyBackend(FakeUnifiedBackend):
    """Devices time out until ``reachable`` is set; counts instantiate calls."""

    def __init__(self) -> None:
        super().__init__(n_devices=1, name="flaky")
        self.reachable = False
        self.instantiate_calls = 0

    def instantiate(self, info):
        self.instantiate_calls += 1
        return info

    def check_connection(self, obj, timeout: float) -> bool:
        return self.reachable


class NoInstantiateBackend(FakeUnifiedBackend):
    """A backend still on the legacy path: inherits DeviceBackend.instantiate."""

    instantiate = DeviceBackend.instantiate


@pytest.fixture(autouse=True)
def _reset_singletons():
    DeviceCatalog.reset_instance()
    DeviceConnectionManager.reset_instance()
    yield
    DeviceConnectionManager.reset_instance()
    DeviceCatalog.reset_instance()


def _status(dev):
    return dev._state.status if dev._state is not None else None


def _load(qtbot, backend):
    # Wait on device_added rather than polling get_device_by_name: its backend
    # fallback caches the device early and the load then skips it as a conflict.
    catalog = DeviceCatalog.get_instance()
    added = []
    catalog.device_added.connect(added.append)
    catalog.add_and_connect_backend(backend)
    qtbot.waitUntil(lambda: bool(added), timeout=3000)
    return catalog, added[0]


def test_failed_connection_marks_device_offline(qtbot):
    backend = FlakyBackend()
    failures: list[str] = []
    catalog = DeviceCatalog.get_instance()
    catalog.device_connection_failed.connect(lambda did, err: failures.append(did))

    catalog, dev = _load(qtbot, backend)

    qtbot.waitUntil(lambda: _status(dev) == DeviceStatus.OFFLINE, timeout=3000)
    assert failures == [str(dev.id)]


def test_failed_retry_returns_to_offline(qtbot):
    backend = FlakyBackend()
    failures: list[str] = []
    catalog, dev = _load(qtbot, backend)
    catalog.device_connection_failed.connect(lambda did, err: failures.append(did))
    qtbot.waitUntil(lambda: _status(dev) == DeviceStatus.OFFLINE, timeout=3000)

    assert catalog.retry_device_connection(dev.id)  # still unreachable

    qtbot.waitUntil(lambda: backend.instantiate_calls == 2, timeout=3000)
    qtbot.waitUntil(lambda: failures == [str(dev.id)], timeout=3000)
    assert _status(dev) == DeviceStatus.OFFLINE
    assert catalog.can_retry_device(dev.id)


def test_reconnect_retries_through_backend(qtbot):
    backend = FlakyBackend()
    catalog, dev = _load(qtbot, backend)
    qtbot.waitUntil(lambda: _status(dev) == DeviceStatus.OFFLINE, timeout=3000)
    assert "_happi_result" not in dev.metadata  # CSM-style: no stashed happi result
    assert catalog.can_retry_device(dev.id)

    backend.reachable = True
    assert catalog.retry_device_connection(dev.id)

    qtbot.waitUntil(lambda: _status(dev) == DeviceStatus.ONLINE, timeout=3000)
    assert backend.instantiate_calls == 2
    assert dev._ophyd_device is dev
    assert not catalog.can_retry_device(dev.id)


def test_retry_refused_for_online_device(qtbot):
    backend = FlakyBackend()
    backend.reachable = True
    catalog, dev = _load(qtbot, backend)
    qtbot.waitUntil(lambda: _status(dev) == DeviceStatus.ONLINE, timeout=3000)

    assert not catalog.can_retry_device(dev.id)
    assert not catalog.retry_device_connection(dev.id)
    assert backend.instantiate_calls == 1


def test_retry_unavailable_without_instantiate_or_happi_result():
    backend = NoInstantiateBackend(n_devices=1, name="flaky")
    catalog = DeviceCatalog.get_instance()
    dev = backend._infos[0]
    catalog._device_cache[dev.id] = dev
    catalog._device_backend_map[dev.id] = backend.name
    catalog._backends[backend.name] = backend
    dev._state = DeviceState(device_id=dev.id, status=DeviceStatus.OFFLINE, connected=False)

    assert not catalog.can_retry_device(dev.id)
    assert not catalog.retry_device_connection(dev.id)
