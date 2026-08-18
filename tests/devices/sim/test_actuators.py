"""Tests for SimShutter."""
import time

from lightfall.devices.sim.actuators import SimShutter


def _wait(status, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not status.done and time.monotonic() < deadline:
        time.sleep(0.01)
    assert status.done and status.success


def test_initial_state_closed():
    sh = SimShutter(name="sh")
    assert sh.state.get() == "closed"
    assert not sh.is_open


def test_open_transitions_through_opening():
    sh = SimShutter(name="sh", travel_time=0.1)
    status = sh.set("open")
    assert sh.state.get() == "opening"
    _wait(status)
    assert sh.state.get() == "open"
    assert sh.is_open


def test_close_after_open():
    sh = SimShutter(name="sh", travel_time=0.05)
    _wait(sh.set("open"))
    status = sh.set("close")
    assert sh.state.get() == "closing"
    _wait(status)
    assert sh.state.get() == "closed"


def test_set_to_current_state_completes_immediately():
    sh = SimShutter(name="sh")
    status = sh.set("close")
    assert status.done and status.success


def test_read_and_describe():
    sh = SimShutter(name="sh")
    reading = sh.read()
    desc = sh.describe()
    assert reading["sh_state"]["value"] == "closed"
    assert desc["sh_state"]["dtype"] == "string"
