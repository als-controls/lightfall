"""Executor-side Tiled connection helpers.

Used by lightfall.exporter. Constructs a ``tiled.client`` with
:class:`~lightfall.auth.service_key_auth.StaticApiKeyAuth` so the executor
authenticates with the Lightfall session API key it received in the NATS job
payload.
"""

from __future__ import annotations

import logging
from typing import Any

from tiled.client import from_uri

from lightfall.auth.service_key_auth import tiled_static_api_key_auth

logger = logging.getLogger(__name__)


def connect_tiled(
    url: str,
    api_key: str | None = None,
    proxy_url: str | None = None,
) -> Any:
    """Connect to a Tiled server and return the client.

    Args:
        url: Tiled server URL.
        api_key: Optional Lightfall-minted Tiled API key secret. When None, the
            client is anonymous.
        proxy_url: Optional SOCKS/HTTP proxy URL (e.g.
            ``socks5://localhost:1080``).

    Returns:
        Tiled client instance.
    """
    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["auth"] = tiled_static_api_key_auth(api_key)
    else:
        logger.warning("connect_tiled: no api_key provided, connecting anonymously")

    if not proxy_url:
        return from_uri(url, **kwargs)

    # Tiled's from_uri probes the URL with httpx.get() and then builds a
    # Transport for the real connection; tiled_proxy routes both through the
    # proxy using tiled's own httpx flavour.
    from lightfall.services import tiled_proxy

    state = tiled_proxy.install(proxy_url)
    try:
        logger.info("Connecting to Tiled at %s via proxy %s", url, proxy_url)
        return from_uri(url, **kwargs)
    finally:
        tiled_proxy.restore(state)


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
