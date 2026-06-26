"""TiledService._subscribe_writer constructs TiledWriter with max_array_size=0.

Task 4b (pystxmcontrol→Lightfall): per-event arrays whose sum(shape) < 16 must
be stored as streamable array nodes, not inlined list columns in the events
table. Setting max_array_size=0 on TiledWriter achieves this — the upstream
writer routes ALL per-event array keys to standalone zarr array nodes rather
than the tabular table, regardless of size.

`TiledWriter` is imported at module-level in tiled_service so it is patchable
there. `get_engine` and `ThreadedTiledWriter` are local imports inside the
method; patch them at their source modules so sys.modules provides the mock.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_subscribe_writer_passes_max_array_size_zero():
    """_subscribe_writer must call TiledWriter(client, batch_size=1, max_array_size=0)."""
    from lightfall.services.tiled_service import TiledService

    svc = TiledService.get_instance()

    # Provide a fake client so the early-exit guard (`if self._client is None`)
    # does not short-circuit the writer construction.
    fake_client = MagicMock(name="fake_tiled_client")
    original_client = svc._client
    svc._client = fake_client

    # Build mock return values so the full construction path executes cleanly.
    mock_raw_writer = MagicMock(name="raw_writer")
    mock_threaded_writer = MagicMock(name="threaded_writer")
    mock_engine = MagicMock(name="engine")

    try:
        with (
            # TiledWriter is imported at module level — patch the module-level name.
            patch(
                "lightfall.services.tiled_service.TiledWriter",
                return_value=mock_raw_writer,
            ) as mock_tw,
            # ThreadedTiledWriter is a local import inside _subscribe_writer;
            # patch its source module so the `from ... import` picks up the mock.
            patch(
                "lightfall.services.threaded_tiled_writer.ThreadedTiledWriter",
                return_value=mock_threaded_writer,
            ),
            # get_engine is also a local import inside _subscribe_writer.
            patch(
                "lightfall.acquire.get_engine",
                return_value=mock_engine,
            ),
        ):
            svc._subscribe_writer()

        # The patched TiledWriter must have been called exactly once …
        mock_tw.assert_called_once()
        _, kwargs = mock_tw.call_args
        # … with batch_size=1 (existing contract) …
        assert kwargs.get("batch_size") == 1, (
            f"expected batch_size=1, got {kwargs.get('batch_size')!r}"
        )
        # … and max_array_size=0 (the new requirement).
        assert kwargs.get("max_array_size") == 0, (
            f"expected max_array_size=0, got {kwargs.get('max_array_size')!r}"
        )

    finally:
        svc._client = original_client
        # Restore writer state so we don't pollute other tests.
        svc._writer = None
        svc._subscription_token = None
