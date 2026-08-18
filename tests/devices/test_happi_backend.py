"""Tests for HappiBackend.load_metadata(), .instantiate(), and .connect() hooks,
and for _guess_category_from_mro() protocol-class mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lightfall.devices.backends.happi import HappiBackend
from lightfall.devices.model import DeviceInfo

# ---------------------------------------------------------------------------
# Helper: build a tiny happi JSON DB in tmp_path with one SynAxis entry
# ---------------------------------------------------------------------------

def _make_happi_db(tmp_path: Path) -> Path:
    """Create a minimal happi JSON database with one SynAxis device."""
    import happi
    from happi.backends.json_db import JSONBackend

    db_path = tmp_path / "test_happi.json"
    # Start with empty JSON so JSONBackend can init cleanly
    db_path.write_text(json.dumps({}))

    db = JSONBackend(str(db_path))
    client = happi.Client(database=db)

    # SynAxis is keyword-only (no positional prefix); set args=[] so happi
    # doesn't try to pass prefix as a positional argument.
    item = happi.OphydItem(
        name="test_motor",
        prefix="",
        device_class="ophyd.sim.SynAxis",
        args=[],
        active=True,
    )
    client.add_item(item)
    return db_path


@pytest.fixture
def happi_backend_with_devices(tmp_path: Path) -> HappiBackend:
    """A HappiBackend connected to a temp JSON db with one device loaded."""
    db_path = _make_happi_db(tmp_path)
    backend = HappiBackend(path=str(db_path))
    assert backend.connect()
    return backend


def _make_backend_on_same_db(backend: HappiBackend) -> HappiBackend:
    """Construct a second HappiBackend pointed at the same JSON path."""
    new_backend = HappiBackend(path=backend.path)
    assert new_backend.connect()
    return new_backend


# ---------------------------------------------------------------------------
# Test 1: load_metadata returns a non-empty list[DeviceInfo] with happi result
# ---------------------------------------------------------------------------

def test_load_metadata_returns_device_info_list(tmp_path: Path) -> None:
    """load_metadata() returns a non-empty list[DeviceInfo]; each info has
    '_happi_result' stashed in metadata."""
    db_path = _make_happi_db(tmp_path)

    backend = HappiBackend(path=str(db_path))
    result = backend.load_metadata()

    assert isinstance(result, list), "load_metadata() must return a list"
    assert len(result) > 0, "load_metadata() must return at least one DeviceInfo"

    for info in result:
        assert isinstance(info, DeviceInfo), f"Expected DeviceInfo, got {type(info)}"
        assert "_happi_result" in info.metadata, (
            f"DeviceInfo '{info.name}' is missing '_happi_result' in metadata"
        )
        assert info.metadata["_happi_result"] is not None

    # Sanity: the one device we added is present
    names = [info.name for info in result]
    assert "test_motor" in names


# ---------------------------------------------------------------------------
# Test 2: instantiate returns the device object via stashed happi result
# ---------------------------------------------------------------------------

def test_instantiate_returns_device_object(tmp_path: Path) -> None:
    """instantiate(info) uses the stashed _happi_result.get() to return
    the ophyd object. We inject a fake SearchResult stub to avoid
    needing hardware or a real EPICS environment."""

    sentinel = object()  # unique object to identify correct return

    class _FakeSearchResult:
        def get(self) -> Any:
            return sentinel

    # Build a DeviceInfo with the fake result stashed
    info = DeviceInfo(
        name="stub_device",
        metadata={"_happi_result": _FakeSearchResult()},
    )

    # Backend doesn't need a real DB for this test — instantiate only looks
    # at info.metadata["_happi_result"]
    backend = HappiBackend(path=None)

    obj = backend.instantiate(info)
    assert obj is sentinel, (
        "instantiate() must return result.get() from the stashed _happi_result"
    )


# ---------------------------------------------------------------------------
# Test 3: instantiate falls back to searching by name when no stashed result
# ---------------------------------------------------------------------------

def test_instantiate_fallback_by_name(tmp_path: Path) -> None:
    """When _happi_result is absent, instantiate() falls back to searching
    the happi client by name. We use a real DB with a SynAxis so the
    ophyd object can actually be constructed."""
    db_path = _make_happi_db(tmp_path)

    backend = HappiBackend(path=str(db_path))
    # Connect so the client is available for the fallback search
    assert backend.connect()

    info = DeviceInfo(name="test_motor")
    # No _happi_result in metadata → should fall back to client.search(name=...)
    obj = backend.instantiate(info)
    # ophyd.sim.SynAxis should be constructable without a real PV
    assert obj is not None


# ---------------------------------------------------------------------------
# Test 4: connect() is session-only — does NOT build devices or run background
# connections
# ---------------------------------------------------------------------------

def test_connect_does_not_start_background_connections(tmp_path: Path) -> None:
    """connect() must NOT instantiate ophyd objects or start background
    connection threads.

    Regression guard for the double-connect bug: the unified load pipeline
    calls backend.connect() + backend.load_metadata() + connect_devices().
    Under the old (broken) code, connect() in "background" mode also called
    _start_background_connections() which ran the phased path — devices would
    be connected twice.

    The fix: _start_background_connections is deleted entirely; connect() may
    populate the CRUD cache (_devices) via _discover_devices() but must never
    call happi_result.get() / instantiate any ophyd object.
    """
    db_path = _make_happi_db(tmp_path)

    backend = HappiBackend(path=str(db_path), instantiate="background")
    assert not backend.is_connected

    ok = backend.connect()

    assert ok, "connect() must return True for a valid happi path"
    assert backend.is_connected, "is_connected must be True after connect()"

    # The happi client must be ready for use (load_metadata() needs it).
    assert backend._client is not None, "connect() must initialise _client"

    # After connect(), no device in the CRUD cache must have an ophyd object —
    # instantiation is the exclusive job of the unified load pipeline's
    # instantiate() step via connect_devices().
    for device in backend._devices.values():
        assert device._ophyd_device is None, (
            f"connect() must NOT instantiate ophyd devices; "
            f"'{device.name}' already has _ophyd_device set"
        )

    # Verify _start_background_connections no longer exists on the class —
    # deleting it prevents accidental re-introduction of the old phased path.
    assert not hasattr(backend, "_start_background_connections"), (
        "_start_background_connections must be removed from HappiBackend"
    )


# ---------------------------------------------------------------------------
# Test 5: _guess_category_from_mro maps bluesky protocol subclasses correctly
# ---------------------------------------------------------------------------

def test_guess_category_maps_bluesky_protocols() -> None:
    """Bluesky-protocol subclasses (ophyd-async devices) get the right category.

    Regression guard: ophyd-async devices don't inherit from classic ophyd
    base classes.  Their MRO contains bluesky.protocols.Movable / Triggerable /
    Flyable instead.  _guess_category_from_mro() must resolve these correctly
    so that PystxmAxis → MOTOR, PystxmCounter → DETECTOR, PystxmLineFlyer → DETECTOR.
    """
    from bluesky.protocols import Flyable, Movable, Triggerable

    from lightfall.devices.backends.happi import _guess_category_from_mro
    from lightfall.devices.model import DeviceCategory

    class _M(Movable): ...  # noqa: E701

    class _T(Triggerable): ...  # noqa: E701

    class _F(Flyable): ...  # noqa: E701

    assert _guess_category_from_mro(_M) == DeviceCategory.MOTOR
    assert _guess_category_from_mro(_T) == DeviceCategory.DETECTOR
    assert _guess_category_from_mro(_F) == DeviceCategory.DETECTOR


# ---------------------------------------------------------------------------
# Test 6: synoptic metadata written to extraneous must round-trip at
# metadata top level (not left nested/buried).
# ---------------------------------------------------------------------------

def test_synoptic_metadata_round_trips(happi_backend_with_devices: HappiBackend) -> None:
    """Regression: synoptic saved to extraneous must come back at top level."""
    backend = happi_backend_with_devices
    infos = backend.load_metadata()
    device = infos[0]
    device.metadata["synoptic"] = {"position": [1.0, 0.0, 0.0], "visible": True}
    assert backend.update_device(device)

    reloaded_backend = _make_backend_on_same_db(backend)  # same JSON path
    reloaded = {i.name: i for i in reloaded_backend.load_metadata()}
    md = reloaded[device.name].metadata
    assert md.get("synoptic", {}).get("position") == [1.0, 0.0, 0.0]
    assert "extraneous" not in md  # never nested


def test_synoptic_metadata_round_trips_through_reload(
    happi_backend_with_devices: HappiBackend,
) -> None:
    """The same lift-to-top-level must also hold via reload()'s
    _add_device_from_result path, not just a fresh load_metadata() call
    on a brand new backend instance."""
    backend = happi_backend_with_devices
    infos = backend.load_metadata()
    device = infos[0]
    device.metadata["synoptic"] = {"position": [2.0, 0.0, 0.0], "visible": True}
    assert backend.update_device(device)

    # A second backend instance, sharing the same JSON db, discovers the
    # device via reload() -> _add_device_from_result (not the initial
    # load_metadata() population point).
    reloaded_backend = _make_backend_on_same_db(backend)
    reloaded_backend.load_metadata()  # establish an initial device set
    assert reloaded_backend.reload()

    md = {i.name: i for i in reloaded_backend.load_metadata()}[device.name].metadata
    assert md.get("synoptic", {}).get("position") == [2.0, 0.0, 0.0]
    assert "extraneous" not in md  # never nested


# ---------------------------------------------------------------------------
# Test 7: scope metadata (non-device) round-trips via pseudo-items
# ---------------------------------------------------------------------------

def test_scope_metadata_round_trips(happi_backend_with_devices: HappiBackend) -> None:
    backend = happi_backend_with_devices
    payload = {"synoptic": {"beam_path": [{"start": [0, 0, 0], "end": [1, 0, 0]}]}}
    assert backend.update_scope_metadata("beamline:bl402", payload)
    assert backend.get_scope_metadata("beamline:bl402") == payload
    # Unknown scope
    assert backend.get_scope_metadata("beamline:nope") is None


# ---------------------------------------------------------------------------
# Test 8: scope pseudo-items never surface as devices
# ---------------------------------------------------------------------------

def test_scope_pseudo_items_are_not_devices(happi_backend_with_devices: HappiBackend) -> None:
    backend = happi_backend_with_devices
    n_before = len(backend.load_metadata())
    backend.update_scope_metadata("beamline:bl402", {"synoptic": {}})
    backend.reload()
    names = [i.name for i in backend.load_metadata()]
    assert len(names) == n_before
    assert not any(n.startswith("_scope_") for n in names)
