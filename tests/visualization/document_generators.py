"""Bluesky document builders and fake Tiled runs for visualization tests.

The JSON fixtures in ``tests/data/visualization/`` hold canonical
start + descriptor document pairs, each annotated with the visualization
that Auto mode should pick and its ``can_handle`` score. The builders here
create ad-hoc documents for edge cases, and :func:`fake_run` wraps a
start/descriptor pair in the minimal surface of a Tiled ``BlueskyRun`` that
``BaseVisualization.can_handle`` and the widgets' ``get_fields`` read.

Ported from the pre-unification ``DocumentProcessor``/``SelectionEngine``
suite (Feb 2026); selection now happens on Tiled entries, so documents are
fed through a fake run instead of a document processor.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent.parent / "data" / "visualization"

# Descriptor keys TiledWriter does not copy into the stream node metadata.
_TILED_WRITER_DROPPED_KEYS = {"name", "object_keys", "run_start"}


@dataclass
class DocumentPair:
    """A start/descriptor pair with the expected Auto-mode winner."""

    start: dict[str, Any]
    descriptor: dict[str, Any]
    expected_viz: str
    expected_score: int
    description: str


def document_pair_from_json(data: dict[str, Any]) -> DocumentPair:
    """Build a :class:`DocumentPair` from a parsed JSON fixture."""
    return DocumentPair(
        start=data["start"],
        descriptor=data["descriptor"],
        expected_viz=data["expected_viz"],
        expected_score=data["expected_score"],
        description=data["description"],
    )


def load_fixture(name: str) -> DocumentPair:
    """Load ``tests/data/visualization/<name>.json``."""
    with open(DATA_DIR / f"{name}.json", encoding="utf-8") as f:
        return document_pair_from_json(json.load(f))


def all_fixture_names() -> list[str]:
    """Names (without extension) of every JSON fixture, sorted."""
    return sorted(p.stem for p in DATA_DIR.glob("*.json"))


def make_uid() -> str:
    return str(uuid.uuid4())


def make_start_doc(
    *,
    plan_name: str = "scan",
    num_points: int | None = None,
    motors: list[str] | None = None,
    hints: dict[str, Any] | None = None,
    shape: list[int] | None = None,
) -> dict[str, Any]:
    """Create a minimal Bluesky start document."""
    doc: dict[str, Any] = {
        "uid": make_uid(),
        "time": 1707600000.0,
        "plan_name": plan_name,
        "scan_id": 1,
    }
    if num_points is not None:
        doc["num_points"] = num_points
    if motors is not None:
        doc["motors"] = motors
    if hints is not None:
        doc["hints"] = hints
    if shape is not None:
        doc["shape"] = shape
    return doc


def make_descriptor_doc(
    *,
    start_uid: str,
    data_keys: dict[str, dict[str, Any]],
    hints: dict[str, Any] | None = None,
    name: str = "primary",
) -> dict[str, Any]:
    """Create a minimal Bluesky event descriptor document.

    ``hints`` uses the event-model layout: ``{object_name: {"fields": [...]}}``.
    """
    return {
        "uid": make_uid(),
        "run_start": start_uid,
        "time": 1707600001.0,
        "name": name,
        "data_keys": data_keys,
        "hints": hints or {},
    }


def make_scalar_data_key(name: str, *, dtype: str = "number") -> dict[str, Any]:
    return {"dtype": dtype, "shape": [], "source": f"SIM:{name}"}


def make_array_data_key(name: str, shape: list[int]) -> dict[str, Any]:
    return {"dtype": "array", "shape": shape, "source": f"SIM:{name}"}


# ---------------------------------------------------------------------------
# Fake Tiled run
# ---------------------------------------------------------------------------


class FakeStream:
    """Metadata-only stand-in for a Tiled ``BlueskyEventStream`` node.

    Field access raises ``KeyError``: these tests exercise selection and
    field ordering, which must not read array data.
    """

    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata

    def __getitem__(self, key: str) -> Any:
        raise KeyError(key)

    def keys(self) -> list[str]:
        return []


class FakeRun:
    """Metadata-only stand-in for a Tiled ``BlueskyRun`` entry."""

    def __init__(self, start: dict[str, Any], streams: dict[str, FakeStream]) -> None:
        self.metadata = {"start": start, "stop": None}
        self._streams = streams

    def __getitem__(self, key: str) -> FakeStream:
        return self._streams[key]

    def __contains__(self, key: object) -> bool:
        return key in self._streams

    def keys(self) -> list[str]:
        return list(self._streams)


def stream_metadata(descriptor: dict[str, Any], layout: str = "tiled_writer") -> dict[str, Any]:
    """Stream-node metadata as a given writer would store ``descriptor``.

    ``tiled_writer``: bluesky's ``TiledWriter`` copies the descriptor
    (minus ``name``/``object_keys``/``run_start``) onto the stream node, so
    ``data_keys`` and ``hints`` sit at the top level.
    ``descriptors``: databroker-style/migrated runs nest whole descriptor
    documents under ``metadata["descriptors"]``.
    """
    if layout == "tiled_writer":
        return {k: v for k, v in descriptor.items() if k not in _TILED_WRITER_DROPPED_KEYS}
    if layout == "descriptors":
        return {"descriptors": [descriptor]}
    raise ValueError(f"unknown layout {layout!r}")


def fake_run(
    start: dict[str, Any],
    *descriptors: dict[str, Any],
    layout: str = "tiled_writer",
) -> FakeRun:
    """Wrap a start document and its descriptors in a :class:`FakeRun`."""
    streams = {
        desc.get("name", "primary"): FakeStream(stream_metadata(desc, layout))
        for desc in descriptors
    }
    return FakeRun(start, streams)


def fake_run_from_pair(pair: DocumentPair, layout: str = "tiled_writer") -> FakeRun:
    return fake_run(pair.start, pair.descriptor, layout=layout)
