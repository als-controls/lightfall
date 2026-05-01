"""Tests for UserSettingsClient — uses pytest-httpx's httpx_mock fixture."""
from __future__ import annotations

import re

import httpx
import pytest


@pytest.fixture(autouse=True)
def _reset_singleton():
    from lucid.settings.user_settings_client import UserSettingsClient
    UserSettingsClient.reset()
    yield
    UserSettingsClient.reset()


def _client(base_url="https://lb.test"):
    from lucid.settings.user_settings_client import UserSettingsClient
    UserSettingsClient.init(base_url=base_url)
    return UserSettingsClient.get_instance()


def test_get_returns_value(httpx_mock):
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/theme?beamline=",
        json={
            "user_id": "alice",
            "beamline": "",
            "key": "theme",
            "value": "dark",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    c = _client()
    assert c.get("theme") == "dark"


def test_get_with_default_swallows_404(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://lb\.test/logbook/settings/missing.*"),
        status_code=404,
    )
    c = _client()
    assert c.get("missing", default="fallback") == "fallback"


def test_get_with_default_swallows_connection_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    c = _client()
    assert c.get("anything", default=None) is None


def test_get_all_returns_dict(httpx_mock):
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings?beamline=",
        json={"theme": "dark", "favorite": ["a", "b"]},
    )
    c = _client()
    assert c.get_all() == {"theme": "dark", "favorite": ["a", "b"]}


def test_beamline_query_passed_through(httpx_mock):
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings?beamline=11.0.1",
        json={},
    )
    c = _client()
    assert c.get_all(beamline="11.0.1") == {}
