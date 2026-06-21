"""Tests for BCSBackend.load_metadata() and .instantiate() hooks.

bcsophyd is ALS-internal and not available in CI; the entire ZMQ layer is
stubbed out so no live server is required.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

from lightfall.devices.backends.bcs import BCSBackend
from lightfall.devices.model import DeviceInfo

# ---------------------------------------------------------------------------
# Helpers: build a minimal bcsophyd stub
# ---------------------------------------------------------------------------

def _make_happi_item(name: str, item_type: str = "motor") -> MagicMock:
    """Return a fake happi SearchResult with .metadata and .get()."""
    sentinel = object()
    item = MagicMock()
    item.metadata = {
        "name": name,
        "itemType": item_type,
        "originalName": name.upper(),
        "units": "mm",
    }
    item.get.return_value = sentinel
    # Store the sentinel so tests can assert on it
    item._sentinel = sentinel
    return item


def _make_bcs_manager(happi_items: list[MagicMock]) -> MagicMock:
    """Return a fake BCSDeviceManager whose .client.items() yields the given items."""
    manager = MagicMock()
    manager.connect = AsyncMock()
    manager.client = MagicMock()
    manager.client.items.return_value = [
        (item.metadata["name"], item) for item in happi_items
    ]
    return manager


def _install_bcsophyd_stub(manager: MagicMock) -> None:
    """Inject a fake bcsophyd.zmq module so BCSBackend.__init__ / connect() won't
    raise ImportError. The fake BCSDeviceManager constructor returns *manager*."""
    bcsophyd_mod = ModuleType("bcsophyd")
    zmq_mod = ModuleType("bcsophyd.zmq")

    mock_cls = MagicMock(return_value=manager)
    zmq_mod.BCSDeviceManager = mock_cls  # type: ignore[attr-defined]
    bcsophyd_mod.zmq = zmq_mod  # type: ignore[attr-defined]

    sys.modules["bcsophyd"] = bcsophyd_mod
    sys.modules["bcsophyd.zmq"] = zmq_mod


def _remove_bcsophyd_stub() -> None:
    sys.modules.pop("bcsophyd", None)
    sys.modules.pop("bcsophyd.zmq", None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def bcs_items() -> list[MagicMock]:
    return [
        _make_happi_item("sample_x", "motor"),
        _make_happi_item("det_diode", "detector"),
    ]


@pytest.fixture()
def bcs_manager(bcs_items: list[MagicMock]) -> MagicMock:
    return _make_bcs_manager(bcs_items)


@pytest.fixture(autouse=True)
def stub_bcsophyd(bcs_manager: MagicMock):
    """Install the bcsophyd stub before each test; tear down after."""
    _install_bcsophyd_stub(bcs_manager)
    yield
    _remove_bcsophyd_stub()


@pytest.fixture()
def connected_backend(bcs_manager: MagicMock) -> BCSBackend:
    """A BCSBackend that has successfully called connect()."""
    backend = BCSBackend(host="fake-host", port=5577)
    assert backend.connect(), "connect() should succeed with stubbed bcsophyd"
    return backend


# ---------------------------------------------------------------------------
# Test 1: load_metadata returns DeviceInfo objects without calling .get()
# ---------------------------------------------------------------------------

def test_load_metadata_returns_device_info_without_instantiating(
    connected_backend: BCSBackend,
    bcs_items: list[MagicMock],
) -> None:
    """load_metadata() returns a non-empty list[DeviceInfo] and must NOT call
    happi_item.get() — instantiation is deferred to instantiate()."""
    # Reset .get() call counts so we only track what load_metadata does
    for item in bcs_items:
        item.get.reset_mock()

    result = connected_backend.load_metadata()

    assert isinstance(result, list), "load_metadata() must return a list"
    assert len(result) > 0, "load_metadata() must return at least one DeviceInfo"
    for info in result:
        assert isinstance(info, DeviceInfo), f"Expected DeviceInfo, got {type(info)}"

    # .get() must NOT have been called during metadata load
    for item in bcs_items:
        item.get.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: instantiate returns the ophyd object via stashed happi item
# ---------------------------------------------------------------------------

def test_instantiate_returns_ophyd_object_from_stash(
    connected_backend: BCSBackend,
    bcs_items: list[MagicMock],
) -> None:
    """instantiate(info) calls .get() on the stashed happi item and returns
    the ophyd object."""
    infos = connected_backend.load_metadata()
    assert infos, "Need at least one DeviceInfo from load_metadata()"

    # Pick the first device
    info = infos[0]
    obj = connected_backend.instantiate(info)

    assert obj is not None, f"instantiate() must return a non-None object for '{info.name}'"

    # Confirm it is the sentinel returned by the matching happi_item.get()
    matching_item = next(
        item for item in bcs_items if item.metadata["name"] == info.name
    )
    assert obj is matching_item._sentinel
