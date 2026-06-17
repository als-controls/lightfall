from __future__ import annotations

from unittest.mock import MagicMock

from lightfall.services.tiled_service import TiledConnectionState, TiledService


def test_adopt_client_sets_client_and_connects():
    TiledService.reset()
    service = TiledService.get_instance()

    fake_client = MagicMock(name="tiled_client")

    service.adopt_client(fake_client, url="https://tiled.nsls2.bnl.gov")

    assert service.client is fake_client
    assert service.is_connected is True
    assert service.state == TiledConnectionState.CONNECTED
    TiledService.reset()
