"""Tests for DeviceConnectionManager two-phase connection."""

import time
import threading
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtCore import QCoreApplication

from lucid.devices.connection_manager import (
    ConnectionResult,
    ConnectionState,
    DeviceConnectionManager,
)


@pytest.fixture
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


@pytest.fixture
def manager(qapp):
    DeviceConnectionManager.reset_instance()
    mgr = DeviceConnectionManager.get_instance()
    yield mgr
    DeviceConnectionManager.reset_instance()


def _make_device_and_result(name="dev", connect_time=0.0):
    """Create a mock DeviceInfo and happi result pair.

    Args:
        name: Device name.
        connect_time: Seconds the mock wait_for_connection should sleep.
    """
    device_info = MagicMock()
    device_info.id = uuid4()
    device_info.name = name

    ophyd_device = MagicMock()
    ophyd_device.wait_for_connection = MagicMock(
        side_effect=lambda timeout=5.0: time.sleep(connect_time)
    )

    happi_result = MagicMock()
    happi_result.get.return_value = ophyd_device

    return device_info, happi_result, ophyd_device


class TestConnectAllPhased:
    def test_instantiation_before_wait(self, manager, qapp):
        """All happi_result.get() calls should complete before any
        wait_for_connection() calls start."""
        call_log = []  # (event, device_name, timestamp)

        devices = []
        for i in range(3):
            info, result, ophyd = _make_device_and_result(f"dev{i}")

            # Capture the ophyd device that .get() should return
            captured_ophyd = ophyd
            def make_get_side_effect(n, dev):
                def side_effect():
                    call_log.append(("get", n, time.monotonic()))
                    return dev
                return side_effect
            result.get.side_effect = make_get_side_effect(f"dev{i}", captured_ophyd)

            def make_wait_side_effect(n):
                def side_effect(timeout=5.0):
                    call_log.append(("wait", n, time.monotonic()))
                return side_effect
            ophyd.wait_for_connection.side_effect = make_wait_side_effect(f"dev{i}")

            devices.append((info, result))

        # Run phased connection
        manager.connect_all_phased(devices, timeout=5.0)

        # Wait for all to finish
        deadline = time.monotonic() + 10.0
        while manager._pending_count > 0 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)

        # Verify ordering: all "get" calls before any "wait" call
        get_times = [t for ev, _, t in call_log if ev == "get"]
        wait_times = [t for ev, _, t in call_log if ev == "wait"]

        assert len(get_times) == 3, f"Expected 3 get calls, got {len(get_times)}"
        assert len(wait_times) == 3, f"Expected 3 wait calls, got {len(wait_times)}"
        assert max(get_times) <= min(wait_times), (
            "All instantiations must finish before any wait_for_connection starts"
        )

    def test_emits_signals_on_success(self, manager, qapp):
        """device_connected should be emitted for each successful device."""
        info, result, ophyd = _make_device_and_result("motor1")

        connected_ids = []
        manager.device_connected.connect(
            lambda r: connected_ids.append(r.device_name)
        )

        manager.connect_all_phased([(info, result)], timeout=5.0)

        deadline = time.monotonic() + 10.0
        while not connected_ids and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)

        assert "motor1" in connected_ids

    def test_failed_instantiation_does_not_block_others(self, manager, qapp):
        """If happi_result.get() raises for one device, others still connect."""
        good_info, good_result, _ = _make_device_and_result("good")
        bad_info, bad_result, _ = _make_device_and_result("bad")
        bad_result.get.side_effect = RuntimeError("import error")

        connected = []
        failed = []
        manager.device_connected.connect(lambda r: connected.append(r.device_name))
        manager.device_failed.connect(lambda r: failed.append(r.device_name))

        manager.connect_all_phased(
            [(bad_info, bad_result), (good_info, good_result)], timeout=5.0
        )

        deadline = time.monotonic() + 10.0
        while len(connected) + len(failed) < 2 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)

        assert "good" in connected
        assert "bad" in failed
