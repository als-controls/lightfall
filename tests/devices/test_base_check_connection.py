"""DeviceBackend.check_connection routes ophyd-async devices to the async path."""
from lightfall.devices import async_connect
from lightfall.devices.base import DeviceBackend


class _StubBackend(DeviceBackend):
    """Concrete backend exposing only what check_connection needs."""
    @property
    def name(self): return "stub"
    @property
    def is_connected(self): return True
    def connect(self): return True
    def disconnect(self): return None
    def get_device(self, device_id): return None
    def get_device_by_name(self, name): return None
    def get_device_by_prefix(self, prefix): return None
    def list_devices(self, category=None, beamline=None, active_only=True): return []
    def search_devices(self, query): return []
    def add_device(self, device): return False
    def update_device(self, device): return False
    def remove_device(self, device_id): return False
    def get_device_configurations(self, device_id): return []
    def get_configuration(self, device_id, config_name): return None
    def save_configuration(self, config): return False
    def delete_configuration(self, config_id): return False
    def get_maintenance_history(self, device_id, limit=100): return []
    def add_maintenance_record(self, record): return False


class _AsyncObj:
    name = "a"
    connected = False
    async def connect(self, mock=False): ...


class _ClassicObj:
    def __init__(self): self.connected = True


def test_async_object_routed_to_connect_async_device(monkeypatch):
    calls = {}
    def fake_connect(obj, *, mock, timeout, loop_wait=5.0):
        calls["mock"] = mock
        calls["timeout"] = timeout
        return True
    monkeypatch.setattr(async_connect, "connect_async_device", fake_connect)
    be = _StubBackend()
    assert be.check_connection(_AsyncObj(), timeout=3.0) is True
    assert calls == {"mock": False, "timeout": 3.0}


def test_connect_mock_attribute_forwarded(monkeypatch):
    seen = {}
    monkeypatch.setattr(async_connect, "connect_async_device",
                        lambda obj, *, mock, timeout, loop_wait=5.0: seen.update(mock=mock) or True)
    be = _StubBackend()
    be._connect_mock = True
    be.check_connection(_AsyncObj(), timeout=1.0)
    assert seen["mock"] is True


def test_classic_object_uses_connected_flag(monkeypatch):
    # If the async helper were called this would raise; ensure it is NOT called.
    monkeypatch.setattr(async_connect, "connect_async_device",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    be = _StubBackend()
    assert be.check_connection(_ClassicObj(), timeout=1.0) is True
