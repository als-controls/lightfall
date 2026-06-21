"""Tests for MockBackend.load_metadata(), .instantiate(), and .check_connection()."""

from __future__ import annotations

from lightfall.devices.backends.mock import MockBackend
from lightfall.devices.model import DeviceInfo

# ---------------------------------------------------------------------------
# Test 1: load_metadata returns a non-empty list[DeviceInfo]
# ---------------------------------------------------------------------------

def test_load_metadata_returns_non_empty_device_info_list() -> None:
    """load_metadata() returns a non-empty list of DeviceInfo without calling connect()."""
    backend = MockBackend()

    result = backend.load_metadata()

    assert isinstance(result, list), "load_metadata() must return a list"
    assert len(result) > 0, "load_metadata() must return at least one DeviceInfo"
    for info in result:
        assert isinstance(info, DeviceInfo), f"Expected DeviceInfo, got {type(info)}"


# ---------------------------------------------------------------------------
# Test 2: instantiate returns a non-None sim object for a known device
# ---------------------------------------------------------------------------

def test_instantiate_returns_sim_object_for_known_device() -> None:
    """instantiate(info) returns a real simulated ophyd object."""
    backend = MockBackend()

    infos = backend.load_metadata()
    assert infos, "Need at least one DeviceInfo from load_metadata()"

    # Pick the first info and instantiate it
    info = infos[0]
    obj = backend.instantiate(info)

    assert obj is not None, f"instantiate() must return a non-None object for '{info.name}'"


# ---------------------------------------------------------------------------
# Test 3: check_connection always returns True for simulated devices
# ---------------------------------------------------------------------------

def test_check_connection_returns_true() -> None:
    """check_connection() returns True immediately for simulated devices."""
    backend = MockBackend()

    # Any object — simulated devices are always "connected"
    result = backend.check_connection(object(), timeout=0.1)

    assert result is True, "check_connection() must return True for mock backend"
