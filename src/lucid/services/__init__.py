"""NCS services module.

This module provides application-level services for NCS:
- TiledService: Manages connection to Tiled data catalog

Usage:
    from lucid.services import TiledService, TiledConnectionState

    service = TiledService.get_instance()
    service.configure(url="http://localhost:8000", enabled=True)
    service.connect()
"""

from lucid.services.tiled_service import (
    TiledConfig,
    TiledConnectionState,
    TiledService,
)

__all__ = [
    "TiledConfig",
    "TiledConnectionState",
    "TiledService",
]
