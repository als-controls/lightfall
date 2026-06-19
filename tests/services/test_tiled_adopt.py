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


def test_adopted_client_survives_late_auto_connect():
    """A background auto-connect that completes AFTER adoption must not clobber
    the adopted client -- the Keycloak-handler race that blanked the browser:
    the auth handler started a connect to the configured URL, which finished
    (~1s later, after a bcgtiled timeout + nsls2 fallback) after the CMS
    bootstrap had already adopted its write-scoped client.
    """
    TiledService.reset()
    service = TiledService.get_instance()

    adopted = MagicMock(name="adopted_client")
    service.adopt_client(adopted, url="https://tiled.nsls2.bnl.gov")

    # Simulate the in-flight auth-triggered connect finishing late.
    service._on_connect_complete(MagicMock(name="root_catalog"))

    assert service.client is adopted
    TiledService.reset()


def test_connect_async_is_noop_when_adopted():
    """Auth-state changes must not start a reconnect over an adopted client."""
    TiledService.reset()
    service = TiledService.get_instance()

    adopted = MagicMock(name="adopted_client")
    service.adopt_client(adopted, url="https://tiled.nsls2.bnl.gov")

    service.connect_async()  # would otherwise spawn a connect thread

    assert service.client is adopted
    TiledService.reset()
