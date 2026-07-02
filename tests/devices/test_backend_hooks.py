from __future__ import annotations

from lightfall.devices.base import DeviceBackend

# check_connection's body does not use `self`; call it unbound with None to
# avoid instantiating the ABC (which has many other abstract methods).


def test_check_connection_uses_wait_for_connection():
    class _Obj:
        def __init__(self):
            self.waited = None

        def wait_for_connection(self, timeout):
            self.waited = timeout

    obj = _Obj()
    assert DeviceBackend.check_connection(None, obj, timeout=2.0) is True
    assert obj.waited == 2.0


def test_check_connection_polls_connected_flag_true():
    class _Obj:
        connected = True

    assert DeviceBackend.check_connection(None, _Obj(), timeout=1.0) is True


def test_check_connection_polls_connected_flag_times_out_false():
    class _Obj:
        connected = False

    assert DeviceBackend.check_connection(None, _Obj(), timeout=0.15) is False
