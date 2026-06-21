"""Tests for HappiBackend.load_metadata() and HappiBackend.instantiate() hooks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
