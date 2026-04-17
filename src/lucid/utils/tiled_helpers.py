"""Tiled client helpers for efficient data access."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from tiled.client.array import ArrayClient


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
