"""Catalog listing dedup, idempotent backend loads, and read-failure status.

Regressions seen on BL12021CHiPPS:
- the device list concatenated each backend's own devices, so devices the load
  pipeline skipped as name conflicts showed up again as stateless duplicates;
- a backend added by the plugin loader was loaded again by connect_backends(),
  rebuilding its DeviceInfo objects with new UUIDs;
- a device whose hardware was absent stayed ONLINE while every read raised.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from lightfall.devices.catalog import DeviceCatalog
from lightfall.devices.connection_manager import DeviceConnectionManager
from lightfall.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus
from tests.devices.test_catalog_unified_load import FakeUnifiedBackend


class FreshIdsBackend(FakeUnifiedBackend):
    """Like HappiBackend: every load_metadata() builds new DeviceInfo objects."""

    def __init__(self, names: list[str], name: str) -> None:
        super().__init__(n_devices=0, name=name)
        self._names = names
        self.loads = 0

    def load_metadata(self) -> list[DeviceInfo]:
        self.loads += 1
        self._infos = [
            DeviceInfo(id=uuid4(), name=n, category=DeviceCategory.MOTOR) for n in self._names
        ]
        return list(self._infos)


@pytest.fixture(autouse=True)
def _reset_singletons():
    DeviceCatalog.reset_instance()
    DeviceConnectionManager.reset_instance()
    yield
    DeviceConnectionManager.reset_instance()
    DeviceCatalog.reset_instance()


def _wait_loaded(qtbot, catalog, names):
    qtbot.waitUntil(lambda: all(catalog.get_device_by_name(n) for n in names), timeout=3000)


# --- Bug A.1: list from the deduplicated cache -------------------------------


def test_two_backends_same_devices_list_once(qtbot):
    catalog = DeviceCatalog.get_instance()
    a = FreshIdsBackend(["m1", "m2"], name="a")
    b = FreshIdsBackend(["m1", "m2"], name="b")  # same file configured twice
    catalog.add_backend(a)
    catalog.add_backend(b)
    seen = []
    catalog.backend_connected.connect(seen.append)
    catalog.connect_backends()
    qtbot.waitUntil(lambda: set(seen) == {"a", "b"}, timeout=3000)

    for listing in (catalog.get_all_devices(), catalog.list_devices(), catalog.search_devices("m")):
        names = sorted(d.name for d in listing)
        assert names == ["m1", "m2"], names
    # Listed objects are the catalog's own (the ones that get state/ophyd)
    for d in catalog.get_all_devices():
        assert catalog.get_device(d.id) is d
        assert catalog.get_device_by_name(d.name) is d


def test_list_filters_apply_to_cached_devices(qtbot):
    catalog = DeviceCatalog.get_instance()
    backend = FakeUnifiedBackend(n_devices=2, name="f")
    backend._infos[1].active = False
    backend._infos[1].category = DeviceCategory.DETECTOR
    catalog.add_and_connect_backend(backend)
    _wait_loaded(qtbot, catalog, [i.name for i in backend._infos])

    assert [d.name for d in catalog.list_devices()] == ["f_dev0"]
    assert len(catalog.list_devices(active_only=False)) == 2
    detectors = catalog.list_devices(category=DeviceCategory.DETECTOR, active_only=False)
    assert [d.name for d in detectors] == ["f_dev1"]


def test_unloaded_backend_devices_still_listed():
    """add_backend() without a load: backend devices still appear (old behaviour)."""
    catalog = DeviceCatalog.get_instance()
    backend = FakeUnifiedBackend(n_devices=2, name="pending")
    catalog.add_backend(backend)
    assert {d.name for d in catalog.get_all_devices()} == backend.device_names


# --- Bug A.3: connect_backends() after add_and_connect_backend() -------------


def test_connect_backends_skips_backend_already_loaded(qtbot):
    catalog = DeviceCatalog.get_instance()
    plugin = FreshIdsBackend(["mte"], name="endstation_devices")
    catalog.add_and_connect_backend(plugin)  # plugin loader, early
    catalog.connect_backends()  # main._setup_devices, moments later
    _wait_loaded(qtbot, catalog, ["mte"])
    qtbot.wait(100)  # let any second load land

    assert plugin.loads == 1
    cached = catalog.get_device_by_name("mte")
    (listed,) = [d for d in catalog.get_all_devices() if d.name == "mte"]
    assert listed is cached
    assert plugin.get_device_by_name("mte") is cached


def test_failed_load_can_be_retried(qtbot):
    catalog = DeviceCatalog.get_instance()
    backend = FreshIdsBackend(["x"], name="flaky")
    original = backend.load_metadata
    calls = {"n": 0}

    def load():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original()

    backend.load_metadata = load
    catalog.add_and_connect_backend(backend)
    qtbot.waitUntil(lambda: "flaky" not in catalog._loading_backends, timeout=3000)
    assert catalog.get_device_by_name("x") is None

    catalog.connect_backends()
    _wait_loaded(qtbot, catalog, ["x"])


def test_remove_then_readd_loads_again(qtbot):
    catalog = DeviceCatalog.get_instance()
    first = FreshIdsBackend(["y"], name="r")
    catalog.add_and_connect_backend(first)
    _wait_loaded(qtbot, catalog, ["y"])
    catalog.remove_backend("r")
    assert catalog.get_device_by_name("y") is None

    second = FreshIdsBackend(["y"], name="r")
    catalog.add_and_connect_backend(second)
    _wait_loaded(qtbot, catalog, ["y"])
    assert second.loads == 1


# --- Bug B: read failures lower the status ------------------------------------


class _AbsentHardware:
    """ophyd-ish object that claims connected but cannot read (unplugged camera)."""

    connected = True

    def __init__(self):
        self.fail = True

    def get(self):
        if self.fail:
            raise RuntimeError("No PICam camera found (serial='02062413')")
        return 1.0


def _online_device(qtbot):
    catalog = DeviceCatalog.get_instance()
    backend = FakeUnifiedBackend(n_devices=1, name="hw")
    catalog.add_and_connect_backend(backend)
    name = backend._infos[0].name

    _wait_loaded(qtbot, catalog, [name])
    device = catalog.get_device_by_name(name)
    hw = _AbsentHardware()
    # What the connection manager leaves behind when ophyd reports connected
    device._ophyd_device = hw
    device._state = DeviceState(device_id=device.id, status=DeviceStatus.ONLINE, connected=True)
    return catalog, device, hw


def test_repeated_read_failures_mark_error_then_recover(qtbot):
    catalog, device, hw = _online_device(qtbot)
    changes = []
    catalog.device_state_changed.connect(lambda did, st: changes.append(st.status))

    for _ in range(DeviceCatalog.READ_FAILURE_THRESHOLD - 1):
        catalog.refresh_device_state(device.id)
        assert device.state.status == DeviceStatus.ONLINE  # one blip isn't an outage

    catalog.refresh_device_state(device.id)
    assert device.state.status == DeviceStatus.ERROR
    assert device.state.connected is False
    assert "PICam" in device.state.additional["read_error"]
    assert DeviceStatus.ERROR in changes

    hw.fail = False
    catalog.refresh_device_state(device.id)
    assert device.state.status == DeviceStatus.ONLINE
    assert device.state.connected is True
    assert device.id not in catalog._read_failures


def test_success_resets_failure_count(qtbot):
    catalog, device, _ = _online_device(qtbot)
    catalog.record_read_failure(device.id, "e")
    catalog.record_read_failure(device.id, "e")
    catalog.record_read_success(device.id)
    catalog.record_read_failure(device.id, "e")
    assert device.state.status == DeviceStatus.ONLINE
