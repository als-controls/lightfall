"""Regression: event-table rows must stay aligned with inline array fields.

Live-scan issue 1 (2026-11-02): on the facility Tiled (PostgreSQL tabular
storage) a run's event table came back out of ``seq_num`` order while the
detector image array (zarr, written at explicit offsets) stayed in
acquisition order, so image row *i* was paired with the wrong motor position.

Cause: tiled's SQL adapter reads ``ORDER BY _partition_id`` only, and every
event row is in partition 0, so PostgreSQL returns *heap* order; one-row
INSERTs spread over pooled backends land on different heap pages once a page
fills (seen in a local PostgreSQL repro as seq 47@(1,1) 48@(2,1) 49@(1,2)
50@(2,2) ...). SQLite returns rowid order, so these tests emulate the
PostgreSQL heap order by permuting the rows every table query returns, the
same way for every query (as a real heap scan would).

Writer stack under test is Lightfall's: ``tiled_writer_patch.TiledWriter``
with ``batch_size=1, max_array_size=0`` inside ``ThreadedTiledWriter``.
"""
from __future__ import annotations

import numpy as np
import pytest

N_EVENTS = 21
FRAME = (256, 256)


def _heap_order(n: int) -> np.ndarray:
    """Even-then-odd row positions, the pattern seen on run db70e1f8."""
    return np.concatenate([np.arange(1, n, 2), np.arange(0, n, 2)])


@pytest.fixture
def tiled_client(tmp_path):
    from tiled.catalog import from_uri as catalog_from_uri
    from tiled.client import Context, from_context
    from tiled.server.app import build_app

    catalog = catalog_from_uri(
        f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}",
        writable_storage=[
            str(tmp_path / "data"),
            f"sqlite:///{(tmp_path / 'internal.db').as_posix()}",
        ],
        init_if_not_exists=True,
    )
    with Context.from_app(build_app(catalog)) as ctx:
        yield from_context(ctx)


@pytest.fixture
def heap_ordered_tables(monkeypatch):
    """Make the SQL adapter return rows in a PostgreSQL-like heap order."""
    from tiled.adapters import sql

    original = sql.SQLAdapter._read_full_table_or_partition

    def scrambled(self, fields=None, partition=None):
        table = original(self, fields=fields, partition=partition)
        return table.take(_heap_order(table.num_rows))

    monkeypatch.setattr(sql.SQLAdapter, "_read_full_table_or_partition", scrambled)


def _run_scan(client, qapp):
    """N events: a 256x256 uint16 image (marks its seq_num) plus scalars."""
    import bluesky.plan_stubs as bps
    import bluesky.preprocessors as bpp
    from bluesky import RunEngine
    from ophyd import Signal

    from lightfall.services.threaded_tiled_writer import ThreadedTiledWriter
    from lightfall.services.tiled_writer_patch import TiledWriter

    pitch = Signal(name="pitch", value=0.0)
    image = Signal(name="image", value=np.zeros(FRAME, dtype=np.uint16))
    positions = np.linspace(-1.0, 1.0, N_EVENTS)

    @bpp.run_decorator()
    def plan():
        for k, pos in enumerate(positions, start=1):
            yield from bps.mv(pitch, float(pos))
            yield from bps.mv(image, np.full(FRAME, k, dtype=np.uint16))
            yield from bps.trigger_and_read([pitch, image])

    writer = ThreadedTiledWriter(TiledWriter(client, batch_size=1, max_array_size=0))
    try:
        RE = RunEngine({})
        RE.subscribe(writer)
        (uid,) = RE(plan())
        assert writer.flush(timeout=60)
        assert writer.stats["errors"] == 0
    finally:
        writer.stop()
    return client[uid], positions


def _check_aligned(stream, positions):
    from lightfall.utils.tiled_helpers import read_events

    events = read_events(stream, variables=["seq_num", "pitch"])
    seq = np.asarray(events["seq_num"])
    assert seq.tolist() == list(range(1, N_EVENTS + 1))
    np.testing.assert_allclose(np.asarray(events["pitch"]), positions)
    # The image array is in acquisition order; frame i was stamped with
    # seq_num i+1, so row i of every table column must belong to event i+1.
    frames = stream.base["image"].read()
    assert frames.shape[0] == N_EVENTS
    np.testing.assert_array_equal(frames[:, 0, 0], seq)


def test_rows_aligned_with_image_after_round_trip(qapp, tiled_client):
    run, positions = _run_scan(tiled_client, qapp)
    assert run.metadata["stop"]["exit_status"] == "success"
    _check_aligned(run["primary"], positions)


def test_rows_aligned_when_server_returns_heap_order(
    qapp, tiled_client, heap_ordered_tables
):
    run, positions = _run_scan(tiled_client, qapp)
    stream = run["primary"]
    # Precondition: the raw table really comes back scrambled, as on the
    # facility server (otherwise this test proves nothing).
    raw = stream.base["internal"].read(["seq_num"])["seq_num"].tolist()
    assert raw != sorted(raw)
    _check_aligned(stream, positions)


def test_reorder_keeps_live_reads_consistent(qapp, tiled_client, heap_ordered_tables):
    """If the table grew between the data read and the reorder query, the
    result keeps the first n events in seq_num order (lengths unchanged)."""
    import xarray

    from lightfall.utils.tiled_helpers import restore_event_order

    run, positions = _run_scan(tiled_client, qapp)
    stream = run["primary"]
    n = 5
    partial = xarray.Dataset({"pitch": ("time", np.zeros(n))})
    out = restore_event_order(stream, partial)
    np.testing.assert_allclose(np.asarray(out["pitch"]), positions[:n])
