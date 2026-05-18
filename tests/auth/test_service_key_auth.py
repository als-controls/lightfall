"""Unit tests for ServiceKeyAuth + StaticApiKeyAuth."""
from __future__ import annotations

import httpx
import pytest

from lucid.auth.service_key_auth import ServiceKeyAuth, StaticApiKeyAuth


def test_static_apikey_auth_sets_header():
    auth = StaticApiKeyAuth("the-secret")
    request = httpx.Request("GET", "https://example/data")
    flow = auth.sync_auth_flow(request)
    out = next(flow)
    assert out.headers["Authorization"] == "Apikey the-secret"


def test_service_key_auth_reads_from_session_manager(monkeypatch):
    captured: dict = {}

    class _FakeSM:
        @classmethod
        def get_instance(cls):
            return cls()
        def get_api_key(self, service):
            captured["service"] = service
            return "tiled-secret-xyz"

    monkeypatch.setattr(
        "lucid.auth.service_key_auth.SessionManager", _FakeSM
    )

    auth = ServiceKeyAuth("tiled")
    request = httpx.Request("GET", "https://example/data")
    flow = auth.sync_auth_flow(request)
    out = next(flow)

    assert captured["service"] == "tiled"
    assert out.headers["Authorization"] == "Apikey tiled-secret-xyz"


def test_service_key_auth_skips_header_when_no_key(monkeypatch):
    """When the cache slot is empty (mint failed at login), yield the request
    without an Authorization header. Downstream call will fail 401 → UI
    surfaces the re-login prompt."""

    class _FakeSM:
        @classmethod
        def get_instance(cls):
            return cls()
        def get_api_key(self, service):
            return None

    monkeypatch.setattr(
        "lucid.auth.service_key_auth.SessionManager", _FakeSM
    )

    auth = ServiceKeyAuth("logbook")
    request = httpx.Request("GET", "https://example/data")
    flow = auth.sync_auth_flow(request)
    out = next(flow)

    assert "Authorization" not in out.headers
