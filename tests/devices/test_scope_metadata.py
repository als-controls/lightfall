"""Tests for the backend scope-metadata API."""
from lightfall.devices.backends.mock import MockBackend
from lightfall.devices.base import DeviceBackend
from lightfall.devices.catalog import DeviceCatalog


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
