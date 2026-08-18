"""Tests for beam path sourcing from scope metadata."""
from lightfall.ui.panels.synoptic.models import BeamPathSegment
from lightfall.ui.panels.synoptic.serialization import (
    load_beam_path_from_catalog,
    merge_beam_path_segments,
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


def test_load_returns_empty_list_when_beam_path_intentionally_empty():
    # An explicit empty list is distinct from "no beam_path key at all":
    # it means the shared beam path was deliberately cleared, not unset.
    segments = load_beam_path_from_catalog(
        FakeCatalog({"synoptic": {"beam_path": []}}), "sim"
    )
    assert segments == []
    assert segments is not None


def test_merge_appends_prefs_segments_not_in_scope():
    scope = [BeamPathSegment(start=(0, 0, 0), end=(1, 0, 0), id="a")]
    prefs = [
        BeamPathSegment(start=(0, 0, 0), end=(9, 9, 9), id="a"),  # dupe id, scope wins
        BeamPathSegment(start=(1, 0, 0), end=(2, 0, 0), id="b"),
    ]
    merged = merge_beam_path_segments(scope, prefs)
    ids = {seg.id for seg in merged}
    assert ids == {"a", "b"}
    winning_a = next(seg for seg in merged if seg.id == "a")
    assert winning_a.end == (1, 0, 0)  # scope's segment, not prefs'


def test_merge_always_appends_segments_with_no_id():
    scope = [BeamPathSegment(start=(0, 0, 0), end=(1, 0, 0), id="a")]
    prefs = [BeamPathSegment(start=(2, 0, 0), end=(3, 0, 0), id=None)]
    merged = merge_beam_path_segments(scope, prefs)
    assert len(merged) == 2


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


def test_merge_dispatch_scope_explicitly_empty_still_merges_in_prefs():
    """Documented intended behavior (see SynopticPanel._load_from_catalog):

    An explicitly-empty shared scope path (``scope: []``, as returned by
    load_beam_path_from_catalog for ``beam_path: []``) is NOT treated the
    same as "no shared path at all" (``scope is None``, which would take
    prefs verbatim without going through the merge function). Both paths
    end up including the user's prefs segments, but only the ``None`` case
    skips ``merge_beam_path_segments`` entirely. This test locks in the
    merge-function behavior for the explicitly-empty-list case: scope=[]
    merged with prefs={b} yields {b}.
    """
    scope_segments: list[BeamPathSegment] = []
    prefs_segments = [BeamPathSegment(start=(0, 0, 0), end=(1, 0, 0), id="b")]

    merged = merge_beam_path_segments(scope_segments, prefs_segments)

    assert merged == prefs_segments
