"""NCS services module.

This module provides application-level services for NCS:
- TiledService: Manages connection to Tiled data catalog

Usage:
    from lightfall.services import TiledService, TiledConnectionState, TiledAuthMode

    service = TiledService.get_instance()
    service.configure(
        url="http://localhost:8000",
        enabled=True,
        auth_mode=TiledAuthMode.KEYCLOAK,
    )
    service.connect_session_manager()  # For Keycloak auth
    # Connection happens automatically when user logs in
"""

from lightfall.services.tiled_auth import KeycloakTiledAuth
from lightfall.services.tiled_service import (
    TiledAuthMode,
    TiledConfig,
    TiledConnectionState,
    TiledService,
)

__all__ = [
    "KeycloakTiledAuth",
    "TiledAuthMode",
    "TiledConfig",
    "TiledConnectionState",
    "TiledService",
]
