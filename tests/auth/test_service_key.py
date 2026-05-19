"""Unit tests for lucid.auth.service_key — mint/revoke against a stub transport."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from lucid.auth.service_key import (
    MintedKey,
    mint_service_key,
    revoke_service_key,
)


def _stub_transport(handler):
    """Wrap an httpx.MockTransport-style handler into a real httpx.Client."""
    return httpx.MockTransport(handler)


@contextmanager
def _patched_httpx(client: httpx.Client):
    """Redirect module-level httpx.post/delete to a real client with a stub transport."""
    import lucid.auth.service_key as mod
    original = mod.httpx

    class _Mod:
        def post(self, url, **kwargs):
            return client.post(url, **kwargs)
        def delete(self, url, **kwargs):
            return client.delete(url, **kwargs)
        HTTPError = httpx.HTTPError
        HTTPStatusError = httpx.HTTPStatusError

    mod.httpx = _Mod()
    try:
        yield
    finally:
        mod.httpx = original


def test_mint_service_key_posts_expected_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "secret": "s" * 64,
                "first_eight": "ssssssss",
                "expiration_time": "2026-05-24T20:14:00+00:00",
                "scopes": ["read:metadata", "read:data"],
                "note": "lucid bcg-ws-3 user123",
            },
        )

    with httpx.Client(transport=_stub_transport(handler)) as client, _patched_httpx(client):
        minted = mint_service_key(
            "https://example/api/v1",
            "bearer-token-xyz",
            expires_in=604800,
            scopes=["read:metadata", "read:data"],
            note="lucid bcg-ws-3 user123",
        )

    assert captured["url"] == "https://example/api/v1/auth/apikey"
    assert captured["headers"]["authorization"] == "Bearer bearer-token-xyz"
    assert b'"expires_in":604800' in captured["body"]
    assert minted.secret == "s" * 64
    assert minted.first_eight == "ssssssss"
    assert minted.expires_at == datetime(2026, 5, 24, 20, 14, tzinfo=UTC)
    assert minted.scopes == ("read:metadata", "read:data")
    assert minted.note == "lucid bcg-ws-3 user123"


def test_mint_service_key_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "no create:apikeys"})

    with httpx.Client(transport=_stub_transport(handler)) as client, _patched_httpx(client):
        with pytest.raises(httpx.HTTPStatusError):
            mint_service_key(
                "https://example/api/v1",
                "bearer-token-xyz",
                expires_in=600,
                scopes=["read:metadata"],
                note="t",
            )


def test_revoke_service_key_swallows_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "kaboom"})

    with httpx.Client(transport=_stub_transport(handler)) as client, _patched_httpx(client):
        # Must NOT raise
        revoke_service_key(
            "https://example/api/v1",
            "bearer-token-xyz",
            first_eight="aaaaaaaa",
        )


def test_mint_service_key_passes_proxy_when_configured(monkeypatch):
    """Regression: when ProxySettingsProvider returns a proxy URL for the
    target, mint_service_key passes it to httpx.post as `proxy=`.

    Without this, *.lbl.gov hosts behind the SSH tunnel time out at login
    because the mint never reaches the server.
    """
    monkeypatch.setattr(
        "lucid.ui.preferences.proxy_settings.ProxySettingsProvider.should_use_proxy_for_url",
        staticmethod(lambda url: "socks5://localhost:1080"),
    )

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "secret": "s" * 64,
                "first_eight": "ssssssss",
                "expiration_time": "2026-05-24T20:14:00+00:00",
                "scopes": ["inherit"],
                "note": "t",
            },
        )

    # Stub httpx.post to capture the kwargs (we can't actually drive a SOCKS
    # transport from here; we just need to confirm proxy was passed through).
    import lucid.auth.service_key as mod
    original_post = mod.httpx.post

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        with httpx.Client(transport=_stub_transport(handler)) as c:
            return c.post(url, **{k: v for k, v in kwargs.items() if k != "proxy"})

    monkeypatch.setattr(mod.httpx, "post", fake_post)

    mint_service_key(
        "https://example.lbl.gov/api/v1",
        "bearer-token-xyz",
        expires_in=604800,
        scopes=["inherit"],
        note="t",
    )

    assert captured["kwargs"].get("proxy") == "socks5://localhost:1080"


def test_mint_service_key_omits_proxy_when_none(monkeypatch):
    """No proxy configured -> proxy kwarg is not passed to httpx.post."""
    monkeypatch.setattr(
        "lucid.ui.preferences.proxy_settings.ProxySettingsProvider.should_use_proxy_for_url",
        staticmethod(lambda url: None),
    )

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "secret": "s" * 64,
                "first_eight": "ssssssss",
                "expiration_time": "2026-05-24T20:14:00+00:00",
                "scopes": ["inherit"],
                "note": "t",
            },
        )

    import lucid.auth.service_key as mod

    def fake_post(url, **kwargs):
        captured["kwargs"] = kwargs
        with httpx.Client(transport=_stub_transport(handler)) as c:
            return c.post(url, **{k: v for k, v in kwargs.items() if k != "proxy"})

    monkeypatch.setattr(mod.httpx, "post", fake_post)

    mint_service_key(
        "https://internal/api/v1",
        "bearer-token-xyz",
        expires_in=604800,
        scopes=["inherit"],
        note="t",
    )

    assert "proxy" not in captured["kwargs"]


def test_minted_key_is_expired():
    past = MintedKey(
        secret="x",
        first_eight="x",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        scopes=(),
        note=None,
    )
    future = MintedKey(
        secret="x",
        first_eight="x",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        scopes=(),
        note=None,
    )
    no_exp = MintedKey(secret="x", first_eight="x", expires_at=None, scopes=(), note=None)
    assert past.is_expired
    assert not future.is_expired
    assert not no_exp.is_expired


def test_revoke_service_key_happy_path():
    """200 response — verify the DELETE call shape, no exception, no return value."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={})

    with httpx.Client(transport=_stub_transport(handler)) as client, _patched_httpx(client):
        result = revoke_service_key(
            "https://example/api/v1",
            "bearer-token-xyz",
            first_eight="abcdefgh",
        )

    assert result is None
    assert captured["method"] == "DELETE"
    assert "first_eight=abcdefgh" in captured["url"]
    assert captured["headers"]["authorization"] == "Bearer bearer-token-xyz"
