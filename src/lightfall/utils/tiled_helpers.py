"""Tiled client helpers for efficient data access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

import numpy as np

if TYPE_CHECKING:
    from tiled.client.array import ArrayClient


def read_events(stream: Any, variables: list[str] | None = None) -> Any | None:
    """Read data variables from a Bluesky event stream.

    ``variables`` limits the read to the named columns (modern streams only).
    Pass it whenever possible: without it, streams carrying detector images
    download every frame.

    Returns an xarray Dataset (or whatever the underlying Tiled layer
    produces) covering all data columns, regardless of whether the stream
    is V3 (CompositeClient with columns as direct children), V2-SQL
    (BlueskyEventStreamV2SQL with a ``data`` subnode), or the very old
    V2-Mongo layout that exposed ``internal/events``.

    The bluesky_tiled_plugins ``BlueskyEventStream.read()`` method already
    abstracts V3 vs V2-SQL, so the modern path is just ``stream.read()``.
    The legacy ``internal/events`` fallback is kept for unmigrated
    deployments that pre-date the SQL backend.

    Returns ``None`` if no readable layout is recognised, with a debug
    log identifying the keys we saw.
    """
    from lightfall.utils.logging import logger

    if stream is None:
        return None

    if variables is not None:
        try:
            return restore_event_order(stream, stream.read(variables=list(variables)))
        except Exception as e:
            # Older clients lack ``variables=``; fall back to a full read.
            logger.debug("read_events: read(variables=...) failed, reading all: {}", e)

    try:
        return restore_event_order(stream, stream.read())
    except Exception as e:
        modern_err = e

    try:
        keys = list(stream.keys())
    except Exception:
        logger.debug("read_events: stream.read() failed and keys() unreadable: {}", modern_err)
        return None

    if "internal" in keys:
        try:
            internal = stream["internal"]
            if "events" in internal:
                return internal["events"].read()
        except Exception as e:
            logger.debug("read_events: legacy internal/events read failed: {}", e)

    logger.debug(
        "read_events: no readable layout (modern err={}; stream keys={})",
        modern_err,
        keys,
    )
    return None


def restore_event_order(stream: Any, events: Any) -> Any:
    """Put the event-table columns of ``events`` back into ``seq_num`` order.

    Tiled's SQL table adapter reads an appendable table with
    ``WHERE _dataset_id=… ORDER BY _partition_id`` (tiled/adapters/sql.py,
    ``_read_full_table_or_partition``). Every row of a Bluesky event table is
    in partition 0, so that ORDER BY is a no-op and PostgreSQL returns rows in
    *heap* order. TiledWriter appends one row per request, and the server's
    connection pool spreads those INSERTs over several backends, each filling
    its own heap page — so once a page fills, rows come back interleaved
    (e.g. evens then odds). The columns stay consistent with each other, but
    array fields (zarr, written at explicit offsets) stay in acquisition
    order, so image row *i* no longer matches scalar row *i*.

    This reads the event table's columns that appear in ``events`` together
    with ``seq_num`` in ONE query, sorts by ``seq_num`` and writes them back,
    so every table column lines up with the array fields again. Non-V3
    layouts, streams without an ``internal`` table and already-ordered
    tables are returned unchanged.
    """
    from lightfall.utils.logging import logger

    try:
        table = stream.base["internal"]
        table_cols = set(table.columns)
    except Exception:
        return events
    if events is None or "seq_num" not in table_cols:
        return events
    names = [
        str(n) for n in list(getattr(events, "variables", {}))
        if str(n) in table_cols
    ]
    if not names:
        return events
    read_cols = names if "seq_num" in names else names + ["seq_num"]
    try:
        n = int(events[names[0]].shape[0])
        df = table.read(read_cols)
    except Exception as e:
        logger.debug("restore_event_order: table re-read failed: {}", e)
        return events
    seq = df["seq_num"].to_numpy()
    if len(seq) < n:
        # Append-only table shrank? Not something we can reason about.
        return events
    order = np.argsort(seq, kind="stable")
    if len(seq) == n and np.array_equal(order, np.arange(n)):
        return events
    # A live run may have appended rows since ``events`` was read; keep the
    # first ``n`` events in seq_num order so lengths match the arrays read
    # alongside them.
    df = df.iloc[order[:n]]
    if len(seq) == n:
        logger.debug(
            "Event table of stream {!r} came back out of seq_num order; "
            "reordered {} rows to keep table columns aligned with arrays",
            getattr(stream, "item", {}).get("id", "?"), n,
        )
    coords = {}
    data = {}
    for name in names:
        var = events[name]
        values = df[name].to_numpy()
        if name in getattr(events, "coords", {}):
            coords[name] = (var.dims, values, var.attrs)
        else:
            data[name] = (var.dims, values, var.attrs)
    if data:
        events = events.assign(data)
    if coords:
        events = events.assign_coords(coords)
    return events


def stream_data_keys(stream: Any) -> dict[str, Any]:
    """Return the ``data_keys`` mapping for a Bluesky event stream.

    Bluesky describes every field of a stream in its Event Descriptor's
    ``data_keys`` (``name -> {shape, dtype, source, ...}``). *Where* that
    mapping lives in Tiled depends on how the run was written:

    * **TiledWriter / modern ``BlueskyEventStream`` nodes** hoist a single
      merged ``data_keys`` to the top level of the stream metadata, so
      ``stream.metadata["data_keys"]`` is populated.
    * **Older databroker-style runs** (and some migrated data) only expose it
      nested inside the descriptor documents, at
      ``stream.metadata["descriptors"][i]["data_keys"]``, with no top-level
      key.

    Code that reads ``stream.metadata.get("data_keys", {})`` directly works on
    the first layout but silently sees ``{}`` on the second — which makes
    visualizations think a run has no fields (e.g. an image run that won't
    open in the Image Stack viewer). Use this helper instead.

    Returns the data_keys regardless of layout, merging across descriptors
    when several are present, or an empty dict if none can be found. Reads
    only already-fetched metadata (no extra Tiled round-trip).
    """
    if stream is None:
        return {}
    try:
        md = stream.metadata
    except Exception:
        return {}

    top = md.get("data_keys")
    if top:
        return dict(top)

    merged: dict[str, Any] = {}
    for desc in md.get("descriptors") or []:
        if isinstance(desc, dict):
            dk = desc.get("data_keys")
            if dk:
                merged.update(dk)
    return merged


def resolve_field_client(stream: Any, field_name: str) -> Any | None:
    """Resolve a field's Tiled ``ArrayClient`` across stream layouts.

    Array fields (detector images, spectra, ...) sit in different places
    depending on how the run was written:

    * **V3 CompositeClient:** the field is a direct child of the stream
      (``stream[field]``).
    * **V2-SQL / databroker-style:** fields live under a ``data/`` subnode
      (``stream["data"][field]``).
    * **External-asset layouts:** under an ``external/`` subnode
      (``stream["external"][field]``).

    Code that only tries ``stream[field]`` (and maybe ``external``) silently
    fails to find images on the ``data/``-subnode layout — the visualization
    is selected but renders nothing. Try each layout and return the first
    that resolves, or ``None``.
    """
    if stream is None:
        return None
    getters = (
        lambda: stream[field_name],
        lambda: stream["data"][field_name],
        lambda: stream["external"][field_name],
    )
    for getter in getters:
        try:
            client = getter()
        except Exception:
            continue
        if client is not None:
            return client
    return None


Slice: TypeAlias = "int | tuple[int, int] | None"


def _build_slice_string(slices: tuple[Slice, ...]) -> str:
    """Build a Tiled ``/array/full/`` slice string.

    Each element of ``slices`` is an ``int`` (single index, drops the axis),
    a ``(start, stop)`` tuple (half-open range), or ``None`` (whole axis).
    """
    parts: list[str] = []
    for sl in slices:
        if sl is None:
            parts.append("::")
        elif isinstance(sl, tuple):
            parts.append(f"{int(sl[0])}:{int(sl[1])}")
        else:
            parts.append(str(int(sl)))
    return ",".join(parts)


def _subcube_shape(slices: tuple[Slice, ...], full_shape: tuple[int, ...]) -> tuple[int, ...]:
    """Resulting shape after applying ``slices`` to an array of ``full_shape``.

    Integer-indexed axes are dropped; ranged and full axes are kept.
    """
    out: list[int] = []
    for sl, dim in zip(slices, full_shape, strict=False):
        if sl is None:
            out.append(int(dim))
        elif isinstance(sl, tuple):
            out.append(int(sl[1]) - int(sl[0]))
        # int -> axis dropped
    return tuple(out)


def fetch_subcube(dataset: ArrayClient, slices: tuple[Slice, ...]) -> np.ndarray:
    """Fetch an arbitrary rectangular sub-volume via server-side slicing.

    Like :func:`fetch_frame` but for any combination of single-index and ranged
    axes. ``slices`` must have one element per array dimension (see
    :func:`_build_slice_string`). Avoids the dask/chunk layer so only the
    requested bytes transfer.

    Returns:
        Array with integer-indexed axes dropped.
    """
    full_shape = tuple(dataset.shape)
    if len(slices) != len(full_shape):
        raise ValueError(
            f"fetch_subcube: got {len(slices)} slice elements for a "
            f"{len(full_shape)}-D array {full_shape}"
        )
    slice_str = _build_slice_string(slices)
    out_shape = _subcube_shape(slices, full_shape)

    url_path = dataset.uri.replace("/metadata/", "/array/full/", 1)
    response = dataset.context.http_client.get(
        url_path,
        headers={"Accept": "application/octet-stream"},
        params={"slice": slice_str},
    )
    response.raise_for_status()

    dtype = dataset.structure().data_type.to_numpy_dtype()
    return np.frombuffer(response.content, dtype=dtype).reshape(out_shape)


def fetch_frame(dataset: ArrayClient, index: int) -> np.ndarray:
    """Fetch a single slice along axis 0 from a Tiled ArrayClient.

    The normal client indexing path (``dataset[i]``) goes through the
    dask/chunk layer and downloads the *entire* chunk. This hits the
    ``/array/full/`` endpoint with a ``slice`` parameter so only the requested
    row transfers. Works for any ndim >= 2.

    Returns:
        Array with shape ``dataset.shape[1:]``.
    """
    n_frames = dataset.shape[0]
    index = int(max(0, min(index, n_frames - 1)))
    slices = (index,) + (None,) * (len(dataset.shape) - 1)
    return fetch_subcube(dataset, slices)
