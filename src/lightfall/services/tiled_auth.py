"""Compatibility shim: previously held KeycloakTiledAuth (bearer-based).

Replaced by lightfall.auth.service_key_auth.ServiceKeyAuth("tiled") in auth-v2.
KeycloakTiledAuth is now a subclass of ServiceKeyAuth that hard-codes the
service name. Existing call sites (``KeycloakTiledAuth()``, ``isinstance(x,
KeycloakTiledAuth)``, subclassing) all keep working.

This shim WILL be deleted in the auth cleanup plan once no in-tree code
imports the old name. Internal Lightfall code should import ServiceKeyAuth
from lightfall.auth.service_key_auth directly going forward.
"""
from __future__ import annotations

from lightfall.auth.service_key_auth import ServiceKeyAuth


class KeycloakTiledAuth(ServiceKeyAuth):
    """Deprecated alias for ServiceKeyAuth("tiled")."""

    def __init__(self) -> None:
        super().__init__("tiled")
