"""Tests for beam path sourcing from scope metadata."""
from lightfall.ui.panels.synoptic.models import BeamPathSegment
from lightfall.ui.panels.synoptic.serialization import (
    load_beam_path_from_catalog,
    save_beam_path_to_catalog,
)


class FakeCatalog:
    def __init__(self, data=None, accept_writes=False):
        self._data = data
        self.accept_writes = accept_writes
        self.written = None

    def get_scope_metadata(self, scope):
        assert scope == "beamline:sim"
        return self._data

    def update_scope_metadata(self, scope, metadata):
        if self.accept_writes:
            self.written = (scope, metadata)
            return True
        return False


def test_load_returns_segments():
    catalog = FakeCatalog({"synoptic": {"beam_path": [
        {"start": [0, 0, 0], "end": [5, 0, 0], "id": "a"},
    ]}})
    segments = load_beam_path_from_catalog(catalog, "sim")
    assert len(segments) == 1
    assert isinstance(segments[0], BeamPathSegment)


def test_load_returns_none_when_scope_unknown():
    assert load_beam_path_from_catalog(FakeCatalog(None), "sim") is None


def test_load_returns_none_when_no_beam_path_key():
    assert load_beam_path_from_catalog(FakeCatalog({"synoptic": {}}), "sim") is None


def test_save_routes_to_catalog_when_accepted():
    catalog = FakeCatalog({"synoptic": {}}, accept_writes=True)
    segments = [BeamPathSegment(start=(0, 0, 0), end=(1, 0, 0), id="a")]
    assert save_beam_path_to_catalog(catalog, "sim", segments)
    scope, metadata = catalog.written
    assert scope == "beamline:sim"
    assert metadata["synoptic"]["beam_path"][0]["id"] == "a"


def test_save_returns_false_when_rejected():
    catalog = FakeCatalog({"synoptic": {}})
    assert not save_beam_path_to_catalog(catalog, "sim", [])
