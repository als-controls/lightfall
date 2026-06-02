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
