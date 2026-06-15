"""TiledService exposes its connected client."""
from __future__ import annotations

from lightfall.services.tiled_service import TiledService


def test_client_property_reflects_internal_client():
    svc = TiledService.get_instance()
    sentinel = object()
    prev = svc._client
    svc._client = sentinel
    try:
        assert svc.client is sentinel
    finally:
        svc._client = prev
