"""Tests for the backend scope-metadata API."""
from lightfall.devices.backends.mock import MockBackend
from lightfall.devices.base import DeviceBackend
from lightfall.devices.catalog import DeviceCatalog


class _FakeScopeBackend(DeviceBackend):
    """Minimal backend that only implements scope metadata get/update.

    ``get_result``/``update_result`` are configurable per-instance so tests
    can script "does not know about this scope" (None) vs. an answer, and
    can observe whether ``update_scope_metadata`` was ever called.
    """

    def __init__(self, name, get_result=None):
        self._name = name
        self._get_result = get_result
        self.update_calls: list[tuple[str, dict]] = []
        self._connected = False

    @property
    def name(self):
        return self._name

    @property
    def is_connected(self):
        return self._connected

    @property
    def is_editable(self):
        return False

    def connect(self):
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    def load_metadata(self):
        return []

    def instantiate(self, info):
        return None

    def check_connection(self, obj, timeout):
        return True

    def get_device(self, device_id):
        return None

    def get_device_by_name(self, name):
        return None

    def get_device_by_prefix(self, prefix):
        return None

    def list_devices(self, category=None, beamline=None, active_only=True):
        return []

    def search_devices(self, query):
        return []

    def add_device(self, device):
        return False

    def update_device(self, device):
        return False

    def remove_device(self, device_id):
        return False

    def get_device_configurations(self, device_id):
        return []

    def get_configuration(self, device_id, config_name):
        return None

    def save_configuration(self, config):
        return False

    def delete_configuration(self, config_id):
        return False

    def get_maintenance_history(self, device_id, limit=100):
        return []

    def add_maintenance_record(self, record):
        return False

    def get_scope_metadata(self, scope):
        return self._get_result

    def update_scope_metadata(self, scope, metadata):
        self.update_calls.append((scope, metadata))
        return True


def test_base_defaults_are_read_nothing():
    backend = MockBackend()
    # MockBackend only overrides beamline:* — an unknown kind exercises
    # the DeviceBackend base defaults.
    assert backend.get_scope_metadata("endstation:saxs") is None
    assert backend.update_scope_metadata("beamline:sim", {}) is False


def test_mock_serves_beam_path_for_any_beamline_scope():
    backend = MockBackend()
    for scope in ("beamline:sim", "beamline:default"):
        data = backend.get_scope_metadata(scope)
        assert data is not None
        assert len(data["synoptic"]["beam_path"]) == 3


def test_mock_scope_metadata_is_a_copy():
    backend = MockBackend()
    data = backend.get_scope_metadata("beamline:sim")
    data["synoptic"]["beam_path"].clear()
    assert len(backend.get_scope_metadata("beamline:sim")["synoptic"]["beam_path"]) == 3


def test_catalog_first_backend_answering_wins(qapp):
    catalog = DeviceCatalog()  # fresh instance, not the singleton
    backend = MockBackend()
    catalog.add_backend(backend)
    data = catalog.get_scope_metadata("beamline:sim")
    assert data is not None
    assert catalog.get_scope_metadata("endstation:none") is None


def test_catalog_update_routes_and_reports(qapp):
    catalog = DeviceCatalog()
    catalog.add_backend(MockBackend())
    # Mock is read-only for scopes -> False
    assert catalog.update_scope_metadata("beamline:sim", {"synoptic": {}}) is False


def test_catalog_multi_backend_routes_to_second_answerer_and_remembers_owner(qapp):
    """First backend registered doesn't know the scope; second does.

    ``get_scope_metadata`` should skip the None-answering first backend and
    return the second's data, while remembering the second as the scope's
    owner. A subsequent ``update_scope_metadata`` should then try that
    remembered owner first: the second backend's update is recorded, and
    the first backend (which never claimed the scope) is left untouched.
    """
    first = _FakeScopeBackend("first", get_result=None)
    second = _FakeScopeBackend("second", get_result={"synoptic": {"beam_path": []}})

    catalog = DeviceCatalog()
    catalog.add_backend(first)
    catalog.add_backend(second)

    data = catalog.get_scope_metadata("beamline:custom")
    assert data == {"synoptic": {"beam_path": []}}

    result = catalog.update_scope_metadata("beamline:custom", {"synoptic": {"beam_path": ["seg"]}})
    assert result is True

    # The remembered owner (second) recorded the write ...
    assert len(second.update_calls) == 1
    assert second.update_calls[0] == ("beamline:custom", {"synoptic": {"beam_path": ["seg"]}})
    # ... and the first backend, which never answered for this scope, was
    # never asked to update it.
    assert first.update_calls == []
