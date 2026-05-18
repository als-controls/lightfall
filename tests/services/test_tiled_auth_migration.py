"""Asserts the Tiled auth path uses ServiceKeyAuth (auth-v2), not the bearer.

Regression coverage for the migration of ``KeycloakTiledAuth`` to
``ServiceKeyAuth("tiled")``. The legacy class name is preserved as a
compatibility shim for out-of-tree consumers; in-tree code uses
``ServiceKeyAuth`` directly.
"""
from __future__ import annotations

import httpx


def test_keycloaktiledauth_shim_returns_service_key_auth() -> None:
    """The compat shim still imports and is a ServiceKeyAuth subclass.

    Out-of-tree consumers can keep doing ``KeycloakTiledAuth()`` until the
    auth cleanup plan deletes the alias.
    """
    from lucid.auth.service_key_auth import ServiceKeyAuth
    from lucid.services.tiled_auth import KeycloakTiledAuth

    obj = KeycloakTiledAuth()
    assert isinstance(obj, ServiceKeyAuth)


def test_service_key_auth_for_tiled_pulls_from_cache(monkeypatch) -> None:
    """``ServiceKeyAuth("tiled")`` reads the cached secret and emits Apikey."""
    from lucid.auth.service_key_auth import ServiceKeyAuth

    class _SM:
        @classmethod
        def get_instance(cls):
            return cls()

        def get_api_key(self, service):
            assert service == "tiled"
            return "the-tiled-key"

    monkeypatch.setattr("lucid.auth.service_key_auth.SessionManager", _SM)

    auth = ServiceKeyAuth("tiled")
    req = httpx.Request("GET", "https://tiled.test/api/v1/metadata/")
    out = next(auth.sync_auth_flow(req))
    assert out.headers["Authorization"] == "Apikey the-tiled-key"


def test_keycloaktiledauth_shim_also_emits_apikey(monkeypatch) -> None:
    """End-to-end: instantiating the shim and running its auth flow yields
    an ``Apikey`` header (proving the shim wires through to ServiceKeyAuth).
    """
    from lucid.services.tiled_auth import KeycloakTiledAuth

    class _SM:
        @classmethod
        def get_instance(cls):
            return cls()

        def get_api_key(self, service):
            assert service == "tiled"
            return "shim-key"

    monkeypatch.setattr("lucid.auth.service_key_auth.SessionManager", _SM)

    auth = KeycloakTiledAuth()
    req = httpx.Request("GET", "https://tiled.test/api/v1/metadata/")
    out = next(auth.sync_auth_flow(req))
    assert out.headers["Authorization"] == "Apikey shim-key"
