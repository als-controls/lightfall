"""Unit tests for TiledService._patch_tiled_for_proxy WS path.

The HTTP/REST proxy patch already has its own integration tests; this
file covers the WS-side addition introduced for the websockets-doesn't-
honor-SOCKS bug.
"""
from __future__ import annotations

import ssl
from unittest.mock import MagicMock, patch

import pytest

from lucid.services.tiled_service import TiledService


def test_ws_patch_routes_socks_through_python_socks(monkeypatch):
    """Calling tiled.client.stream.connect after the patch should open a
    SOCKS-tunnelled socket via python_socks and pass it as sock= to the
    real connect()."""
    import tiled.client.stream as stream_mod

    socks_sock = MagicMock(name="socks_socket")
    proxy_obj = MagicMock(name="proxy")
    proxy_obj.connect.return_value = socks_sock

    real_connect_calls: list = []

    def fake_real_connect(uri, **kwargs):
        real_connect_calls.append((uri, kwargs))
        return MagicMock(name="websocket_connection")

    # Stash the original to restore at end of test
    original_stream_connect_before_patch = stream_mod.connect
    stream_mod.connect = fake_real_connect

    try:
        with patch("python_socks.sync.Proxy.from_url", return_value=proxy_obj):
            restore_info = TiledService._patch_tiled_for_proxy(
                "socks5://localhost:1080"
            )
            assert restore_info is not None, "patch returned None"

            # The patch replaced stream_mod.connect with a wrapper
            assert stream_mod.connect is not fake_real_connect
            # And captured fake_real_connect as the "original" to delegate to
            assert restore_info["original_stream_connect"] is fake_real_connect

            # Call through the wrapper; assert SOCKS path runs
            stream_mod.connect("ws://bcgtiled.test:8000/api/v1/stream/single/x")

            proxy_obj.connect.assert_called_once_with("bcgtiled.test", 8000)
            assert len(real_connect_calls) == 1
            uri, kwargs = real_connect_calls[0]
            assert uri == "ws://bcgtiled.test:8000/api/v1/stream/single/x"
            assert kwargs["sock"] is socks_sock
            assert kwargs["server_hostname"] == "bcgtiled.test"
            assert "ssl" not in kwargs  # ws:// — no TLS

            # Restore for the test's own cleanliness
            stream_mod.connect = restore_info["original_stream_connect"]
            restore_info["context_mod"].Transport = restore_info[
                "original_transport_cls"
            ]
            restore_info["context_mod"].httpx.get = restore_info[
                "original_httpx_get"
            ]
    finally:
        stream_mod.connect = original_stream_connect_before_patch


def test_ws_patch_adds_ssl_for_wss(monkeypatch):
    """Same as above but with wss:// — must inject an SSLContext."""
    import tiled.client.stream as stream_mod

    socks_sock = MagicMock()
    proxy_obj = MagicMock()
    proxy_obj.connect.return_value = socks_sock
    real_connect_calls: list = []

    def fake_real_connect(uri, **kwargs):
        real_connect_calls.append((uri, kwargs))
        return MagicMock()

    original_before = stream_mod.connect
    stream_mod.connect = fake_real_connect

    try:
        with patch("python_socks.sync.Proxy.from_url", return_value=proxy_obj):
            restore_info = TiledService._patch_tiled_for_proxy(
                "socks5://localhost:1080"
            )
            stream_mod.connect("wss://bcgtiled.test/api/v1/stream/single/x")

            proxy_obj.connect.assert_called_once_with("bcgtiled.test", 443)
            uri, kwargs = real_connect_calls[0]
            assert isinstance(kwargs["ssl"], ssl.SSLContext)
            assert kwargs["server_hostname"] == "bcgtiled.test"

            # Cleanup
            stream_mod.connect = restore_info["original_stream_connect"]
            restore_info["context_mod"].Transport = restore_info[
                "original_transport_cls"
            ]
            restore_info["context_mod"].httpx.get = restore_info[
                "original_httpx_get"
            ]
    finally:
        stream_mod.connect = original_before


def test_ws_patch_passthrough_for_non_socks_proxy():
    """For HTTP-CONNECT proxies, the wrapper should not inject a sock and
    should delegate to the real connect (websockets handles HTTP CONNECT
    internally via proxy=True default)."""
    import tiled.client.stream as stream_mod

    real_connect_calls: list = []

    def fake_real_connect(uri, **kwargs):
        real_connect_calls.append((uri, kwargs))
        return MagicMock()

    original_before = stream_mod.connect
    stream_mod.connect = fake_real_connect

    try:
        restore_info = TiledService._patch_tiled_for_proxy(
            "http://corporate-proxy:3128"
        )
        if restore_info is None:
            pytest.skip("HTTP proxy transport not constructible in this env")
        stream_mod.connect("ws://bcgtiled.test:8000/api/v1/stream/single/x")

        assert len(real_connect_calls) == 1
        uri, kwargs = real_connect_calls[0]
        # No SOCKS path — no sock= or ssl=
        assert "sock" not in kwargs
        assert "ssl" not in kwargs

        # Cleanup
        stream_mod.connect = restore_info["original_stream_connect"]
        restore_info["context_mod"].Transport = restore_info[
            "original_transport_cls"
        ]
        restore_info["context_mod"].httpx.get = restore_info[
            "original_httpx_get"
        ]
    finally:
        stream_mod.connect = original_before
