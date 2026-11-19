"""Route ``tiled.client`` connections through a SOCKS or HTTP proxy.

``tiled.client.from_uri`` talks to the server in two places, and both have to
go through the proxy:

1. ``Context.from_any_uri`` probes the URL with a module-level
   ``httpx.get(uri, timeout=..., trust_env=...)`` to detect an HTTP->HTTPS
   redirect.
2. ``Context.__init__`` builds its client around
   ``tiled.client.context.Transport(cache=..., limits=..., verify=...,
   trust_env=...)``.

tiled>=0.2.21 is built on ``httpx2`` (``import httpx2 as httpx``); older
releases on ``httpx``. Everything here is built from *tiled's* module
(``tiled.client.context.httpx``) so the proxy transport, the probe client and
the response are all the flavour tiled expects. Both flavours support
``socks5://`` proxies natively when ``socksio`` is installed.
"""

from __future__ import annotations

from typing import Any

# Keyword arguments of the module-level ``httpx.get`` that configure the
# *client* rather than the request; ``Client.get`` rejects them.
_CLIENT_KWARGS = ("trust_env", "verify", "cert", "timeout", "follow_redirects")

_PROBE_TIMEOUT = 15.0


def tiled_httpx() -> Any:
    """The httpx module tiled's client uses (``httpx2`` or ``httpx``)."""
    import tiled.client.context as context_mod

    return context_mod.httpx


def make_proxy_transport(proxy_url: str, **kwargs: Any) -> Any:
    """A transport of tiled's httpx flavour that routes through ``proxy_url``.

    Raises ImportError for a SOCKS URL when ``socksio`` is missing.
    """
    return tiled_httpx().HTTPTransport(proxy=proxy_url, **kwargs)


def install(proxy_url: str) -> dict[str, Any]:
    """Patch tiled so new connections go through ``proxy_url``.

    Returns the state :func:`restore` needs. Raises if the proxy transport
    cannot be built (e.g. ImportError for SOCKS without ``socksio``), before
    anything is patched.
    """
    import tiled.client.context as context_mod
    from tiled.client.transport import Transport as OriginalTransport

    httpx_mod = context_mod.httpx
    proxy_transport = make_proxy_transport(proxy_url)

    original_get = httpx_mod.get

    def proxy_get(url: Any, **kwargs: Any) -> Any:
        client_kwargs = {k: kwargs.pop(k) for k in _CLIENT_KWARGS if k in kwargs}
        client_kwargs.setdefault("timeout", _PROBE_TIMEOUT)
        transport = make_proxy_transport(
            proxy_url, verify=client_kwargs.get("verify", True)
        )
        with httpx_mod.Client(transport=transport, **client_kwargs) as client:
            return client.get(url, **kwargs)

    class ProxyTransport(OriginalTransport):
        """tiled's caching Transport around the proxy-aware inner transport."""

        def __init__(self, *, transport: Any = None, **kwargs: Any) -> None:
            super().__init__(transport=proxy_transport, **kwargs)

    original_transport_cls = context_mod.Transport
    httpx_mod.get = proxy_get
    context_mod.Transport = ProxyTransport
    return {
        "context_mod": context_mod,
        "httpx_mod": httpx_mod,
        "original_get": original_get,
        "original_transport_cls": original_transport_cls,
    }


def restore(state: dict[str, Any] | None) -> None:
    """Undo :func:`install`. Safe to call with None."""
    if not state:
        return
    state["context_mod"].Transport = state["original_transport_cls"]
    state["httpx_mod"].get = state["original_get"]
