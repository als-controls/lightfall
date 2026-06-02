"""Tiled client helpers for efficient data access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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


def fetch_frame(dataset: ArrayClient, index: int) -> np.ndarray:
    """Fetch a single slice along axis 0 from a Tiled ArrayClient.

    The normal client indexing path (``dataset[i]``) goes through the
    dask/chunk layer and downloads the *entire* chunk, which can be all
    frames.  This function hits the ``/array/full/`` endpoint directly
    with a ``slice`` parameter so only the requested row is transferred.

    Works for any ndim >= 2 (image stacks, flat GP arrays, etc.).

    Args:
        dataset: A Tiled ArrayClient with shape ``(N, ...)``.
        index: Index along the first axis.

    Returns:
        Array with shape ``dataset.shape[1:]``.
    """
    n_frames = dataset.shape[0]
    index = int(max(0, min(index, n_frames - 1)))
    frame_shape = dataset.shape[1:]

    # Build slice string for arbitrary ndim: "idx,::,::,..."
    slice_parts = [str(index)] + ["::" for _ in range(len(dataset.shape) - 1)]
    slice_str = ",".join(slice_parts)

    url_path = dataset.uri.replace("/metadata/", "/array/full/", 1)
    response = dataset.context.http_client.get(
        url_path,
        headers={"Accept": "application/octet-stream"},
        params={"slice": slice_str},
    )
    response.raise_for_status()

    dtype = dataset.structure().data_type.to_numpy_dtype()
    return np.frombuffer(response.content, dtype=dtype).reshape(frame_shape)
