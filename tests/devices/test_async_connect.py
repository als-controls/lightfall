"""connect_async_device drives await obj.connect() on the engine loop."""
import asyncio
import threading

import pytest

from lightfall.devices import async_connect


class _AsyncDevice:
    """Minimal ophyd-async-like device: awaitable connect()."""
    def __init__(self):
        self.name = "fake"
        self.connected = False
        self.connect_calls = []

    async def connect(self, mock=False, timeout=10.0, force_reconnect=False):
        self.connect_calls.append(mock)
        self.connected = True


class _ClassicDevice:
    """Classic ophyd-like device: sync connect-ish surface."""
    def __init__(self):
        self.name = "classic"
    def wait_for_connection(self, timeout=None):
        pass


class _RunningLoop:
    """A real asyncio loop running forever on a daemon thread."""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._t = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._t.start()
        while not self.loop.is_running():
            pass

    @property
    def event_loop(self):
        return self.loop

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._t.join(timeout=2.0)
        self.loop.close()


@pytest.fixture
def running_engine(monkeypatch):
    eng = _RunningLoop()
    monkeypatch.setattr(async_connect, "get_engine", lambda: eng)
    yield eng
    eng.close()


def test_is_async_connectable_detects_coroutine_connect():
    assert async_connect.is_async_connectable(_AsyncDevice()) is True
    assert async_connect.is_async_connectable(_ClassicDevice()) is False
    assert async_connect.is_async_connectable(object()) is False


def test_connect_async_device_awaits_connect(running_engine):
    dev = _AsyncDevice()
    ok = async_connect.connect_async_device(dev, mock=False, timeout=2.0)
    assert ok is True
    assert dev.connected is True
    assert dev.connect_calls == [False]


def test_connect_async_device_passes_mock(running_engine):
    dev = _AsyncDevice()
    async_connect.connect_async_device(dev, mock=True, timeout=2.0)
    assert dev.connect_calls == [True]


def test_connect_async_device_no_loop_returns_false(monkeypatch):
    # get_engine returns an engine whose event_loop is None
    monkeypatch.setattr(async_connect, "get_engine",
                        lambda: type("E", (), {"event_loop": None})())
    dev = _AsyncDevice()
    ok = async_connect.connect_async_device(dev, mock=False, timeout=0.5, loop_wait=0.3)
    assert ok is False
    assert dev.connected is False


def test_connect_async_device_connect_raises_returns_false(running_engine):
    class _Boom(_AsyncDevice):
        async def connect(self, mock=False, timeout=10.0, force_reconnect=False):
            raise RuntimeError("nope")
    ok = async_connect.connect_async_device(_Boom(), timeout=2.0)
    assert ok is False
