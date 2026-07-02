"""TiledService._subscribe_writer constructs TiledWriter with max_array_size=0.

Task 4b (pystxmcontrol→Lightfall): per-event arrays whose sum(shape) < 16 must
be stored as streamable array nodes, not inlined list columns in the events
table. Setting max_array_size=0 on TiledWriter achieves this — the upstream
writer routes ALL per-event array keys to standalone zarr array nodes rather
than the tabular table, regardless of size.

Patch targets (verified empirically):

* ``TiledWriter`` is imported at module level in ``tiled_service`` (line 16),
  so it is patched at ``lightfall.services.tiled_service.TiledWriter``.
* ``get_engine`` and ``ThreadedTiledWriter`` are LOCAL imports inside
  ``_subscribe_writer`` (``from lightfall.services.threaded_tiled_writer
  import ThreadedTiledWriter`` etc.). A local ``from X import Y`` executes at
  call time and resolves ``Y`` from the SOURCE module ``X``'s namespace — so
  these are patched at their source modules
  (``lightfall.services.threaded_tiled_writer.ThreadedTiledWriter``,
  ``lightfall.acquire.get_engine``). Patching ``tiled_service.<name>`` would
  NOT work here: the name does not exist on ``tiled_service`` at module level
  (it is never bound there), and even with ``create=True`` the local
  ``from ... import`` reads the source module, bypassing the created name.
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
            # ThreadedTiledWriter is a LOCAL import inside _subscribe_writer;
            # the local `from ... import` resolves it from the source module at
            # call time, so patching the source module is what takes effect.
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

        # The raw TiledWriter must be constructed exactly once with the fake
        # client positionally and both keyword knobs: the existing batch_size=1
        # and the new max_array_size=0 (per-event arrays -> streamable nodes).
        mock_tw.assert_called_once_with(
            fake_client, batch_size=1, max_array_size=0
        )

    finally:
        svc._client = original_client
        # Restore writer state so we don't pollute other tests.
        svc._writer = None
        svc._subscription_token = None
