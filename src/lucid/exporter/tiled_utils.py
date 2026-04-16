"""Tiled client utilities for the exporter."""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

import httpx
from tiled.client import from_uri

logger = logging.getLogger(__name__)


class BearerAuth(httpx.Auth):
    """Simple httpx.Auth that adds a static Bearer token."""

    def __init__(self, token: str) -> None:
        self._token = token

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


def connect_tiled(
    url: str,
    token: str | None = None,
    proxy_url: str | None = None,
) -> Any:
    """Connect to a Tiled server and return the client.

    Args:
        url: Tiled server URL.
        token: Optional auth token (Bearer token for Keycloak).
        proxy_url: Optional SOCKS/HTTP proxy URL (e.g. ``socks5://localhost:1080``).

    Returns:
        Tiled client instance.
    """
    kwargs: dict[str, Any] = {}
    if token:
        kwargs["auth"] = BearerAuth(token)

    if not proxy_url:
        return from_uri(url, **kwargs)

    # Tiled's from_uri calls httpx.get() for a redirect check and then
    # creates a Transport for the real connection.  Both need to go
    # through the proxy.
    import httpx
    import tiled.client.context as context_mod
    from tiled.client.transport import Transport as OriginalTransport

    proxy_transport = httpx.HTTPTransport(proxy=proxy_url)

    original_httpx_get = context_mod.httpx.get

    def proxy_httpx_get(u, **kw):
        with httpx.Client(proxy=proxy_url, timeout=15.0) as client:
            return client.get(u, **kw)

    context_mod.httpx.get = proxy_httpx_get  # type: ignore[attr-defined]

    class ProxyTransport(OriginalTransport):
        def __init__(self, *, transport=None, **kw):
            super().__init__(transport=proxy_transport, **kw)

    original_transport_cls = context_mod.Transport
    context_mod.Transport = ProxyTransport

    try:
        logger.info("Connecting to Tiled at %s via proxy %s", url, proxy_url)
        return from_uri(url, **kwargs)
    finally:
        context_mod.Transport = original_transport_cls
        context_mod.httpx.get = original_httpx_get


def get_run(client: Any, uid: str) -> Any:
    """Look up a run by UID in a Tiled catalog.

    Args:
        client: Tiled client (catalog).
        uid: Run UID.

    Returns:
        Tiled run container.

    Raises:
        KeyError: If run not found.
    """
    return client[uid]
