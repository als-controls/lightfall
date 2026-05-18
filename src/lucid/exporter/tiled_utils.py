"""Executor-side Tiled connection helpers.

Used by lucid.exporter. Constructs a ``tiled.client`` with
:class:`~lucid.auth.service_key_auth.StaticApiKeyAuth` so the executor
authenticates with the LUCID session API key it received in the NATS job
payload.
"""

from __future__ import annotations

import logging
from typing import Any

from tiled.client import from_uri

from lucid.auth.service_key_auth import StaticApiKeyAuth

logger = logging.getLogger(__name__)


def connect_tiled(
    url: str,
    api_key: str | None = None,
    proxy_url: str | None = None,
) -> Any:
    """Connect to a Tiled server and return the client.

    Args:
        url: Tiled server URL.
        api_key: Optional LUCID-minted Tiled API key secret. When None, the
            client is anonymous.
        proxy_url: Optional SOCKS/HTTP proxy URL (e.g.
            ``socks5://localhost:1080``).

    Returns:
        Tiled client instance.
    """
    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["auth"] = StaticApiKeyAuth(api_key)

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
