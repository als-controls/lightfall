"""lightfall.services.tiled_proxy: tiled's probe and Transport go through the proxy.

tiled>=0.2.21 calls ``httpx.get(uri, timeout=..., trust_env=...)`` (httpx2)
for its redirect probe; the old proxy patch forwarded ``trust_env`` to
``Client.get`` and Lightfall failed to connect with "unexpected keyword
argument 'trust_env'".
"""

from __future__ import annotations

import pytest
import tiled.client.context as context_mod

from lightfall.services import tiled_proxy


@pytest.fixture
def mock_proxy(monkeypatch):
    """Replace the proxy transport with a MockTransport of tiled's flavour."""
    httpx_mod = tiled_proxy.tiled_httpx()
    seen: list = []

    def handler(request):
        seen.append(request)
        return httpx_mod.Response(200, json={"via": "proxy"})

    made: list = []

    def fake_make(proxy_url, **kwargs):
        made.append((proxy_url, kwargs))
        return httpx_mod.MockTransport(handler)

    monkeypatch.setattr(tiled_proxy, "make_proxy_transport", fake_make)
    return seen, made


def test_probe_accepts_client_kwargs_and_uses_proxy(mock_proxy):
    seen, made = mock_proxy
    original_get = context_mod.httpx.get
    state = tiled_proxy.install("socks5://localhost:1080")
    try:
        # Exactly how tiled 0.2.22's Context.from_any_uri calls it.
        response = context_mod.httpx.get(
            "http://tiled.example/api/v1/", timeout=5.0, trust_env=True
        )
    finally:
        tiled_proxy.restore(state)
    assert response.status_code == 200
    assert isinstance(response, tiled_proxy.tiled_httpx().Response)
    assert [str(r.url) for r in seen] == ["http://tiled.example/api/v1/"]
    assert all(url == "socks5://localhost:1080" for url, _ in made)
    assert context_mod.httpx.get is original_get


def test_transport_wraps_proxy_and_restores(mock_proxy):
    seen, _ = mock_proxy
    original_cls = context_mod.Transport
    state = tiled_proxy.install("socks5://localhost:1080")
    try:
        assert context_mod.Transport is not original_cls
        # tiled constructs it with these keywords (trust_env is new in 0.2.21).
        transport = context_mod.Transport(
            cache=None, limits=None, verify=True, trust_env=True
        )
        httpx_mod = tiled_proxy.tiled_httpx()
        with httpx_mod.Client(transport=transport) as client:
            assert client.get("http://tiled.example/x").json() == {"via": "proxy"}
    finally:
        tiled_proxy.restore(state)
    assert seen and context_mod.Transport is original_cls


def test_restore_none_is_noop():
    tiled_proxy.restore(None)
