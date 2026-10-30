"""Disabling a device drops and destroys its live ophyd object; enabling rebuilds it.

Devices that hold exclusive hardware (e.g. a camera opened through a vendor SDK)
must be released when the user disables them in the device tree, so another
program can use the hardware. Disabled devices are also not instantiated at load.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from lightfall.devices.catalog import DeviceCatalog
from lightfall.devices.connection_manager import DeviceConnectionManager
from lightfall.devices.model import DeviceCategory, DeviceInfo, DeviceStatus
from tests.devices.test_catalog_unified_load import FakeUnifiedBackend


class FakeHardware:
    """Stand-in ophyd object that records destroy()."""

    instances: list[FakeHardware] = []

    def __init__(self, name: str) -> None:
        self.name = name
        self.destroyed = False
        FakeHardware.instances.append(self)

    def destroy(self) -> None:
        self.destroyed = True


class HardwareBackend(FakeUnifiedBackend):
    """Editable backend whose instantiate() builds a FakeHardware object."""

    def __init__(self, active=(True,)) -> None:
        super().__init__(n_devices=0, name="hw")
        self._infos = [
            DeviceInfo(id=uuid4(), name=f"hw_dev{i}", category=DeviceCategory.DETECTOR, active=a)
            for i, a in enumerate(active)
        ]
        self.instantiate_calls = 0
        self.cache: dict[str, FakeHardware] = {}

    def instantiate(self, info):
        # Cache like happi does (SearchResult._instantiated / happi.loader.cache).
        self.instantiate_calls += 1
        if info.name not in self.cache:
            self.cache[info.name] = FakeHardware(info.name)
        return self.cache[info.name]

    def release_instance(self, info, obj):
        if self.cache.get(info.name) is obj:
            del self.cache[info.name]

    @property
    def is_editable(self) -> bool:
        return True

    def update_device(self, device) -> bool:
        return any(d.id == device.id for d in self._infos)

    def remove_device(self, device_id) -> bool:
        before = len(self._infos)
        self._infos = [d for d in self._infos if d.id != device_id]
        return len(self._infos) != before


@pytest.fixture(autouse=True)
def _reset_singletons():
    DeviceCatalog.reset_instance()
    DeviceConnectionManager.reset_instance()
    FakeHardware.instances = []
    yield
    DeviceConnectionManager.reset_instance()
    DeviceCatalog.reset_instance()


def _status(dev):
    return dev._state.status if dev._state is not None else None


def _load(qtbot, backend, n=1):
    catalog = DeviceCatalog.get_instance()
    added = []
    catalog.device_added.connect(added.append)
    catalog.add_and_connect_backend(backend)
    qtbot.waitUntil(lambda: len(added) == n, timeout=3000)
    return catalog, added


def test_disable_destroys_live_object_and_enable_rebuilds(qtbot):
    backend = HardwareBackend()
    catalog, (dev,) = _load(qtbot, backend)
    qtbot.waitUntil(lambda: dev._ophyd_device is not None, timeout=3000)
    first = dev._ophyd_device

    dev.active = False
    assert catalog.update_device(dev)
    assert dev._ophyd_device is None
    assert first.destroyed
    assert _status(dev) == DeviceStatus.INACTIVE
    assert catalog.get_ophyd_device(dev.name) is None

    dev.active = True
    assert catalog.update_device(dev)
    qtbot.waitUntil(lambda: dev._ophyd_device is not None, timeout=3000)
    second = dev._ophyd_device
    assert second is not first and not second.destroyed
    assert _status(dev) == DeviceStatus.ONLINE
    assert backend.instantiate_calls == 2


def test_disabled_devices_are_not_instantiated_at_load(qtbot):
    backend = HardwareBackend(active=(True, False))
    catalog, added = _load(qtbot, backend, n=2)
    on, off = sorted(added, key=lambda d: d.name)
    qtbot.waitUntil(lambda: on._ophyd_device is not None, timeout=3000)
    assert off._ophyd_device is None
    assert _status(off) == DeviceStatus.INACTIVE
    assert backend.instantiate_calls == 1

    off.active = True
    catalog.update_device(off)
    qtbot.waitUntil(lambda: off._ophyd_device is not None, timeout=3000)


def test_other_edits_keep_live_object(qtbot):
    backend = HardwareBackend()
    catalog, (dev,) = _load(qtbot, backend)
    qtbot.waitUntil(lambda: dev._ophyd_device is not None, timeout=3000)
    obj = dev._ophyd_device
    dev.display_name = "Renamed"
    assert catalog.update_device(dev)
    assert dev._ophyd_device is obj and not obj.destroyed


def test_remove_destroys_live_object(qtbot):
    backend = HardwareBackend()
    catalog, (dev,) = _load(qtbot, backend)
    qtbot.waitUntil(lambda: dev._ophyd_device is not None, timeout=3000)
    obj = dev._ophyd_device
    assert catalog.remove_device(dev.id)
    assert obj.destroyed


def test_happi_backend_release_instance_evicts_caches():
    pytest.importorskip("happi")
    from happi import loader

    from lightfall.devices.backends.happi import HappiBackend

    obj = object()

    class Result:
        _instantiated = obj

    info = DeviceInfo(id=uuid4(), name="cached_dev", category=DeviceCategory.DETECTOR,
                      metadata={"_happi_result": Result()})
    loader.cache["cached_dev"] = obj
    try:
        HappiBackend(path=None).release_instance(info, obj)
        assert info.metadata["_happi_result"]._instantiated is None
        assert "cached_dev" not in loader.cache
    finally:
        loader.cache.pop("cached_dev", None)
