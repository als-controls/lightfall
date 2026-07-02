"""Tests for DeviceConnectionManager.connect_devices — bounded-concurrency engine."""

from __future__ import annotations

import time

import pytest

from lightfall.devices.base import DeviceBackend
from lightfall.devices.connection_manager import ConnectionState, DeviceConnectionManager
from lightfall.devices.model import DeviceInfo

# ---------------------------------------------------------------------------
# Fake backend
# ---------------------------------------------------------------------------

def _make_backend(infos):  # noqa: ARG001 — infos unused but kept for API symmetry
    class _B(DeviceBackend):
        @property
        def name(self):
            return "fakeconn"

        @property
        def is_connected(self):
            return True

        def connect(self):
            return True

        def disconnect(self):
            return None

        def load_metadata(self):
            return infos

        def instantiate(self, info):
            return info  # stand-in object carrying .name

        def check_connection(self, obj, timeout):
            if obj.name == "slow":
                time.sleep(timeout + 0.3)
                return False  # times out
            return True

        # remaining abstract query methods unused in this test:
        def get_device(self, device_id):
            ...

        def get_device_by_name(self, name):
            ...

        def get_device_by_prefix(self, prefix):
            ...

        def list_devices(self, category=None, beamline=None, active_only=True):
            return []

        def search_devices(self, query):
            return []

        def add_device(self, device):
            return True

        def update_device(self, device):
            return True

        def remove_device(self, device_id):
            return True

        def get_device_configurations(self, device_id):
            return []

        def get_configuration(self, device_id, config_name):
            return None

        def save_configuration(self, config):
            return True

        def delete_configuration(self, config_id):
            return True

        def get_maintenance_history(self, device_id, limit=100):
            return []

        def add_maintenance_record(self, record):
            return True

    return _B()


def _make_infos(*names):
    return [DeviceInfo(name=n) for n in names]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_manager():
    """Always start with a fresh singleton so tests don't bleed state."""
    DeviceConnectionManager.reset_instance()
    yield
    DeviceConnectionManager.reset_instance()


# ---------------------------------------------------------------------------
# Test 1 — isolation under bounded concurrency
# ---------------------------------------------------------------------------

def test_slow_device_does_not_block_fast_devices(qtbot):
    """One slow (timeout) device must NOT block fast ones from completing.

    fast1 and fast2 should reach CONNECTED before 'slow' finishes its
    long sleep, and 'slow' must eventually land in a terminal failed state.
    """
    infos = _make_infos("fast1", "slow", "fast2")
    backend = _make_backend(infos)
    manager = DeviceConnectionManager.get_instance()

    connected_names: list[str] = []
    failed_names: list[str] = []

    manager.device_connected.connect(lambda r: connected_names.append(r.device_name))
    manager.device_failed.connect(lambda r: failed_names.append(r.device_name))

    # timeout=0.2s; slow sleeps for 0.5s (0.2 + 0.3 extra); max_concurrency=3
    manager.connect_devices(backend, infos, timeout=0.2, max_concurrency=3)

    # Fast devices should be connected before slow finishes
    qtbot.waitUntil(
        lambda: {"fast1", "fast2"} <= set(connected_names),
        timeout=2000,
    )
    # At this point the slow device must NOT yet be in the connected list
    assert "slow" not in connected_names, (
        "'slow' should still be in-flight while fast devices are already connected"
    )

    # Slow device must eventually reach a terminal failed/timeout state
    qtbot.waitUntil(
        lambda: "slow" in failed_names,
        timeout=3000,
    )

    # Final sanity checks
    assert set(connected_names) == {"fast1", "fast2"}
    slow_id = next(i.id for i in infos if i.name == "slow")
    assert manager.get_state(slow_id) in (ConnectionState.FAILED, ConnectionState.TIMEOUT)


# ---------------------------------------------------------------------------
# Test 2 — all fast devices reach CONNECTED and DeviceInfo gets populated
# ---------------------------------------------------------------------------

def test_all_fast_devices_connected_and_ophyd_set(qtbot):
    """All 3 fast devices reach CONNECTED and each DeviceInfo._ophyd_device is set."""
    infos = _make_infos("motor1", "motor2", "motor3")
    backend = _make_backend(infos)
    manager = DeviceConnectionManager.get_instance()

    connected_names: list[str] = []
    manager.device_connected.connect(lambda r: connected_names.append(r.device_name))

    manager.connect_devices(backend, infos, timeout=1.0, max_concurrency=12)

    qtbot.waitUntil(
        lambda: set(connected_names) == {"motor1", "motor2", "motor3"},
        timeout=3000,
    )

    # Each DeviceInfo should have its ophyd device set to the stand-in object
    for info in infos:
        assert info._ophyd_device is not None, (
            f"DeviceInfo '{info.name}' should have _ophyd_device set after CONNECTED"
        )
        # The fake backend returns `info` itself as the stand-in
        assert info._ophyd_device is info

    # All states recorded as CONNECTED
    for info in infos:
        assert manager.get_state(info.id) == ConnectionState.CONNECTED


# ---------------------------------------------------------------------------
# Test 3 — all_connections_complete fires once when the batch drains
#
# These drive a QApplication directly (rather than pytest-qt's qtbot) so they
# run in a runtime venv without the test extras; QApplication.instance() reuses
# pytest-qt's qapp when the full suite runs in CI.
# ---------------------------------------------------------------------------

def _spin(app, cond, timeout=3.0):
    """Pump the Qt event loop until *cond* is true or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()


def test_all_connections_complete_emitted_once_after_batch():
    """``all_connections_complete`` fires exactly once after every device in
    the batch reaches a terminal state — this is the "devices loaded" event
    the CMS session gate waits on. Today the batch path never emits it."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    infos = _make_infos("m1", "m2", "m3")
    backend = _make_backend(infos)
    manager = DeviceConnectionManager.get_instance()

    completed: list[int] = []
    manager.all_connections_complete.connect(lambda: completed.append(1))

    manager.connect_devices(backend, infos, timeout=1.0, max_concurrency=2)

    _spin(app, lambda: completed == [1])
    # Give any stray late emission a chance to (incorrectly) arrive.
    time.sleep(0.1)
    app.processEvents()
    assert completed == [1]


def test_all_connections_complete_emitted_even_with_timeout():
    """A device that times out must NOT prevent the batch-complete signal —
    otherwise the CMS gate would hang whenever an IOC is down."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    infos = _make_infos("fast", "slow")
    backend = _make_backend(infos)
    manager = DeviceConnectionManager.get_instance()

    completed: list[int] = []
    manager.all_connections_complete.connect(lambda: completed.append(1))

    manager.connect_devices(backend, infos, timeout=0.2, max_concurrency=2)

    _spin(app, lambda: completed == [1])
    time.sleep(0.1)
    app.processEvents()
    assert completed == [1]


def test_all_connections_complete_emitted_for_empty_batch():
    """An empty device set still fires the signal (synchronously) so the gate
    never hangs waiting on a backend that registered no devices."""
    backend = _make_backend([])
    manager = DeviceConnectionManager.get_instance()

    completed: list[int] = []
    manager.all_connections_complete.connect(lambda: completed.append(1))

    manager.connect_devices(backend, [], timeout=1.0)

    assert completed == [1]
