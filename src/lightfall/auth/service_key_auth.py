"""httpx.Auth adapters for Lightfall's per-service API keys.

ServiceKeyAuth pulls the current API key from SessionManager's cache on
every request — used by in-process consumers (Lightfall's own data-browser,
RE callback writers, etc.) that share the singleton SessionManager.

StaticApiKeyAuth captures a literal secret at construction time — used by
out-of-process consumers (lightfall.exporter executor, lightfall-pipelines
executor, tsuchinoko executor) that receive the key in their NATS job
payload and have no SessionManager singleton.

Both produce the same wire behavior: `Authorization: Apikey <secret>`.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

import httpx

from lightfall.auth.session import SessionManager


class ServiceKeyAuth(httpx.Auth):
    """httpx.Auth that reads a service's API key from SessionManager.

    Construct one instance per service name:
        ServiceKeyAuth("tiled")
        ServiceKeyAuth("logbook")

    Reads on every request so a re-login that refreshes the cache is picked
    up without rebuilding the underlying client.
    """

    def __init__(self, service: str) -> None:
        self._service = service

    def _set_header(self, request: httpx.Request) -> bool:
        """Set Authorization if a key is cached; return True if set."""
        secret = SessionManager.get_instance().get_api_key(self._service)
        if secret is None:
            return False
        request.headers["Authorization"] = f"Apikey {secret}"
        return True

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        self._set_header(request)
        yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        self._set_header(request)
        yield request


class StaticApiKeyAuth(httpx.Auth):
    """httpx.Auth that injects a captured literal API key.

    Used by executor subprocesses (exporter, pipelines, tsuchinoko) that
    receive the key in their job payload and have no SessionManager
    singleton.
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Apikey {self._secret}"
        yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Apikey {self._secret}"
        yield request


# --- Tiled-compatible variants --------------------------------------------
#
# tiled>=0.2.21 moved its client from ``httpx`` to ``httpx2`` and
# ``tiled.client.context.Context`` rejects any ``auth`` that is not an
# instance of *its* ``httpx.Auth`` ("Tiled client auth parameter has been set
# with <class ServiceKeyAuth>. httpx.Auth or None allowed."). The classes
# above stay on plain httpx for Lightfall's own httpx clients (logbook, CSM,
# scopes); anything handed to ``tiled.client.from_uri(auth=...)`` must be
# built with the factories below, which subclass whichever Auth base the
# installed tiled uses (httpx2 on >=0.2.21, httpx on older releases).


def _tiled_auth_base() -> type:
    """``Auth`` base class of the httpx flavour the installed tiled client uses."""
    from tiled.client import context as tiled_context

    return tiled_context.httpx.Auth


_TILED_CLASSES: dict[type, tuple[type, type]] = {}


def _tiled_classes() -> tuple[type, type]:
    base = _tiled_auth_base()
    classes = _TILED_CLASSES.get(base)
    if classes is None:
        tiled_service = type(
            "TiledServiceKeyAuth",
            (base,),
            {
                "__doc__": "ServiceKeyAuth on tiled's httpx flavour.",
                "__init__": ServiceKeyAuth.__init__,
                "_set_header": ServiceKeyAuth._set_header,
                "sync_auth_flow": ServiceKeyAuth.sync_auth_flow,
                "async_auth_flow": ServiceKeyAuth.async_auth_flow,
            },
        )
        tiled_static = type(
            "TiledStaticApiKeyAuth",
            (base,),
            {
                "__doc__": "StaticApiKeyAuth on tiled's httpx flavour.",
                "__init__": StaticApiKeyAuth.__init__,
                "sync_auth_flow": StaticApiKeyAuth.sync_auth_flow,
                "async_auth_flow": StaticApiKeyAuth.async_auth_flow,
            },
        )
        classes = _TILED_CLASSES[base] = (tiled_service, tiled_static)
    return classes


def tiled_service_key_auth(service: str = "tiled"):
    """ServiceKeyAuth equivalent accepted by ``tiled.client.from_uri(auth=...)``."""
    return _tiled_classes()[0](service)


def tiled_static_api_key_auth(secret: str):
    """StaticApiKeyAuth equivalent accepted by ``tiled.client.from_uri(auth=...)``."""
    return _tiled_classes()[1](secret)
