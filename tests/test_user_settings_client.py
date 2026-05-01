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


def test_set_posts_value(httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url="https://lb.test/logbook/settings/theme",
        match_json={"value": "dark", "beamline": ""},
        json={
            "user_id": "alice",
            "beamline": "",
            "key": "theme",
            "value": "dark",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    c = _client()
    c.set("theme", "dark")  # no return value checked


def test_set_with_beamline(httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url="https://lb.test/logbook/settings/k",
        match_json={"value": [1, 2], "beamline": "11.0.1"},
        json={
            "user_id": "alice",
            "beamline": "11.0.1",
            "key": "k",
            "value": [1, 2],
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    c = _client()
    c.set("k", [1, 2], beamline="11.0.1")


def test_set_raises_on_5xx(httpx_mock):
    from lucid.settings.user_settings_client import UserSettingsError

    httpx_mock.add_response(
        method="PUT",
        url="https://lb.test/logbook/settings/x",
        status_code=500,
    )
    c = _client()
    with pytest.raises(UserSettingsError):
        c.set("x", "y")


def test_set_raises_on_network_error(httpx_mock):
    from lucid.settings.user_settings_client import UserSettingsError

    httpx_mock.add_exception(httpx.ConnectError("boom"))
    c = _client()
    with pytest.raises(UserSettingsError):
        c.set("x", "y")


def test_delete_succeeds(httpx_mock):
    httpx_mock.add_response(
        method="DELETE",
        url="https://lb.test/logbook/settings/theme?beamline=",
        status_code=204,
    )
    c = _client()
    c.delete("theme")


def test_delete_treats_404_as_success(httpx_mock):
    """Delete is idempotent — 404 (already gone) is success, not an error."""
    httpx_mock.add_response(
        method="DELETE",
        url=re.compile(r"https://lb\.test/logbook/settings/missing.*"),
        status_code=404,
    )
    c = _client()
    c.delete("missing")  # no exception expected


def test_delete_raises_on_5xx(httpx_mock):
    from lucid.settings.user_settings_client import UserSettingsError

    httpx_mock.add_response(
        method="DELETE",
        url=re.compile(r"https://lb\.test/logbook/settings/x.*"),
        status_code=500,
    )
    c = _client()
    with pytest.raises(UserSettingsError):
        c.delete("x")


def test_upload_image_returns_id(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://lb.test/logbook/images",
        json={"image_id": "abc-123", "mime_type": "image/png", "size_bytes": 42},
        status_code=201,
    )
    c = _client()
    image_id = c.upload_image(b"\x89PNG fake bytes", "image/png")
    assert image_id == "abc-123"


def test_upload_image_raises_on_4xx(httpx_mock):
    from lucid.settings.user_settings_client import UserSettingsError

    httpx_mock.add_response(
        method="POST",
        url="https://lb.test/logbook/images",
        status_code=400,
        json={"detail": "too big"},
    )
    c = _client()
    with pytest.raises(UserSettingsError):
        c.upload_image(b"x" * 10, "image/png")


def test_image_url_builds_absolute():
    c = _client()
    assert (
        c.image_url("abc-123")
        == "https://lb.test/logbook/images/abc-123"
    )


def test_download_image_returns_bytes_and_mime(httpx_mock):
    httpx_mock.add_response(
        url="https://lb.test/logbook/images/img-1",
        content=b"BYTES",
        headers={"content-type": "image/png"},
    )
    c = _client()
    data, mime = c.download_image("img-1")
    assert data == b"BYTES"
    assert mime == "image/png"


def test_download_image_raises_on_404(httpx_mock):
    from lucid.settings.user_settings_client import UserSettingsError

    httpx_mock.add_response(
        url="https://lb.test/logbook/images/missing",
        status_code=404,
    )
    c = _client()
    with pytest.raises(UserSettingsError):
        c.download_image("missing")
