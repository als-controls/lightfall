"""Tests for SimTemperatureController."""
from lightfall.devices.sim.actuators import SimTemperatureController


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _read(tc):
    tc.readback.trigger()
    return tc.readback.get()


def test_initial_readback_matches_initial():
    clock = FakeClock()
    tc = SimTemperatureController(name="tc", initial=295.0, clock=clock)
    assert _read(tc) == 295.0


def test_readback_slews_toward_setpoint():
    clock = FakeClock()
    tc = SimTemperatureController(name="tc", rate=2.0, initial=295.0, clock=clock)
    tc.setpoint.put(305.0)
    clock.t = 1.0  # 1 s later: moved 2.0 K of the 10 K step
    assert abs(_read(tc) - 297.0) < 1e-6
    clock.t = 100.0  # long after: arrived, no overshoot
    assert _read(tc) == 305.0


def test_setpoint_change_mid_slew_reanchors():
    clock = FakeClock()
    tc = SimTemperatureController(name="tc", rate=2.0, initial=295.0, clock=clock)
    tc.setpoint.put(305.0)
    clock.t = 1.0
    _read(tc)  # 297.0
    tc.setpoint.put(290.0)  # re-anchor at current value, head down
    clock.t = 2.0  # 1 s after re-anchor
    assert abs(_read(tc) - 295.0) < 1e-6
