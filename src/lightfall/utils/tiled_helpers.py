"""Tiled client helpers for efficient data access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

import numpy as np

if TYPE_CHECKING:
    from tiled.client.array import ArrayClient


def read_events(stream: Any) -> Any | None:
    """Read all data variables from a Bluesky event stream.

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

    try:
        return stream.read()
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
    for sl, dim in zip(slices, full_shape):
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
