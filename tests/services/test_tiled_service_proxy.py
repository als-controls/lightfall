"""Unit tests for the module-level WS-proxy patch on tiled.client.stream.connect.

The patch is installed at import time of lucid.services.tiled_service. The
wrapper does a live ProxySettingsProvider lookup per call, so we test by
controlling that lookup's return value.
"""
from __future__ import annotations

import ssl
from unittest.mock import MagicMock, patch

import pytest

# Importing the module also installs the patch.
import lucid.services.tiled_service  # noqa: F401
import tiled.client.stream as stream_mod


@pytest.fixture
def fake_real_connect():
    """Replace the wrapped 'original_stream_connect' captured inside our
    wrapper with a recorder, by reaching through the closure.

    The wrapper is a closure that captured the original connect when first
    installed. We can't re-stub the wrapper itself without losing the SUT,
    so we patch the upstream import path. The wrapper resolves the inner
    delegate via its closure cell — we replace the cell's contents.
    """
    wrapper = stream_mod.connect
    assert getattr(wrapper, "_lucid_socks_patched", False), (
        "test premise: lucid.services.tiled_service must install the patch on import"
    )
    # The closure has one cell: the original connect.
    original_cell = wrapper.__closure__[0]

    calls: list = []

    def recorder(uri, **kwargs):
        calls.append((uri, kwargs))
        return MagicMock(name="ws_connection")

    # Save + swap the cell contents. (CPython lets us mutate cell_contents.)
    saved = original_cell.cell_contents
    original_cell.cell_contents = recorder
    try:
        yield calls
    finally:
        original_cell.cell_contents = saved


def test_ws_wrapper_routes_socks_when_provider_returns_socks_url(fake_real_connect):
    """ProxySettingsProvider returns a socks5 URL → wrapper opens a SOCKS
    tunnel via python_socks and passes sock= to the inner connect."""
    socks_sock = MagicMock(name="socks_socket")
    proxy_obj = MagicMock(name="proxy")
    proxy_obj.connect.return_value = socks_sock

    with patch(
        "lucid.ui.preferences.proxy_settings.ProxySettingsProvider"
        ".should_use_proxy_for_url",
        return_value="socks5://localhost:1080",
    ), patch("python_socks.sync.Proxy.from_url", return_value=proxy_obj):
        stream_mod.connect("ws://bcgtiled.test:8000/api/v1/stream/single/x")

    proxy_obj.connect.assert_called_once_with("bcgtiled.test", 8000)
    assert len(fake_real_connect) == 1
    uri, kwargs = fake_real_connect[0]
    assert uri == "ws://bcgtiled.test:8000/api/v1/stream/single/x"
    assert kwargs["sock"] is socks_sock
    assert kwargs["server_hostname"] == "bcgtiled.test"
    assert "ssl" not in kwargs


def test_ws_wrapper_adds_ssl_for_wss(fake_real_connect):
    socks_sock = MagicMock()
    proxy_obj = MagicMock()
    proxy_obj.connect.return_value = socks_sock

    with patch(
        "lucid.ui.preferences.proxy_settings.ProxySettingsProvider"
        ".should_use_proxy_for_url",
        return_value="socks5://localhost:1080",
    ), patch("python_socks.sync.Proxy.from_url", return_value=proxy_obj):
        stream_mod.connect("wss://bcgtiled.test/api/v1/stream/single/x")

    proxy_obj.connect.assert_called_once_with("bcgtiled.test", 443)
    assert len(fake_real_connect) == 1
    _, kwargs = fake_real_connect[0]
    assert isinstance(kwargs["ssl"], ssl.SSLContext)
    assert kwargs["server_hostname"] == "bcgtiled.test"


def test_ws_wrapper_passthrough_when_no_proxy(fake_real_connect):
    """ProxySettingsProvider returns None → wrapper delegates without
    injecting sock or ssl."""
    with patch(
        "lucid.ui.preferences.proxy_settings.ProxySettingsProvider"
        ".should_use_proxy_for_url",
        return_value=None,
    ):
        stream_mod.connect("ws://bcgtiled.test:8000/api/v1/stream/single/x")

    assert len(fake_real_connect) == 1
    _, kwargs = fake_real_connect[0]
    assert "sock" not in kwargs
    assert "ssl" not in kwargs


def test_ws_wrapper_passthrough_for_non_socks_proxy(fake_real_connect):
    """HTTP-CONNECT proxies aren't handled by python_socks; the wrapper
    delegates to upstream websockets (which can use its own proxy= default
    if HTTPS_PROXY is set in env)."""
    with patch(
        "lucid.ui.preferences.proxy_settings.ProxySettingsProvider"
        ".should_use_proxy_for_url",
        return_value="http://corporate-proxy:3128",
    ):
        stream_mod.connect("ws://bcgtiled.test:8000/api/v1/stream/single/x")

    assert len(fake_real_connect) == 1
    _, kwargs = fake_real_connect[0]
    assert "sock" not in kwargs
    assert "ssl" not in kwargs


def test_install_is_idempotent():
    """Calling _install_tiled_stream_ws_proxy_patch a second time is a no-op."""
    from lucid.services.tiled_service import _install_tiled_stream_ws_proxy_patch

    wrapper_before = stream_mod.connect
    _install_tiled_stream_ws_proxy_patch()
    wrapper_after = stream_mod.connect
    assert wrapper_before is wrapper_after
