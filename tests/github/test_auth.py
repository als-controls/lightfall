"""Tests for GitHub device-flow and PAT sign-in calls (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from lightfall.github import DEFAULT_CLIENT_ID, auth
from lightfall.github.auth import (
    DeviceCode,
    GitHubAuthError,
    TokenGrant,
    credential_from_grant,
    credential_from_pat,
    fetch_identity,
    poll_for_token,
    request_device_code,
    validate_pat,
)

PAT = "github_pat_SECRET123"


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def code(interval: int = 5, expires_in: int = 900) -> DeviceCode:
    return DeviceCode(
        "dev123", "ABCD-1234", "https://github.com/login/device", interval, expires_in
    )


def scripted(responses: list[dict]):
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(it))

    return make_client(handler)


def test_device_code_parsed():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        seen["accept"] = request.headers["Accept"]
        return httpx.Response(
            200,
            json={
                "device_code": "dev123",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            },
        )

    dc = request_device_code("cid", client=make_client(handler))
    assert dc.user_code == "ABCD-1234"
    assert dc.device_code == "dev123"
    assert dc.interval == 5 and dc.expires_in == 900
    assert "dev123" not in repr(dc)
    assert seen["url"] == "https://github.com/login/device/code"
    assert "client_id=cid" in seen["body"]
    assert seen["accept"] == "application/json"


def test_poll_pending_then_success():
    sleeps = []
    client = scripted(
        [
            {"error": "authorization_pending"},
            {"error": "authorization_pending"},
            {"access_token": "ghu_tok", "expires_in": 28800, "refresh_token": "ghr_x"},
        ]
    )
    grant = poll_for_token("cid", code(), client=client, sleep=sleeps.append)
    assert grant.access_token == "ghu_tok"
    assert grant.expires_in == 28800
    assert sleeps == [5, 5]
    assert "ghu_tok" not in repr(grant)


def test_poll_sends_expected_form():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "t"})

    grant = poll_for_token("cid", code(), client=make_client(handler), sleep=lambda s: None)
    assert grant.expires_in is None
    assert seen["url"] == "https://github.com/login/oauth/access_token"
    assert "device_code=dev123" in seen["body"]
    assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code" in seen["body"]


def test_poll_slow_down_increases_interval():
    sleeps = []
    client = scripted(
        [
            {"error": "authorization_pending"},
            {"error": "slow_down"},
            {"access_token": "t"},
        ]
    )
    poll_for_token("cid", code(), client=client, sleep=sleeps.append)
    assert sleeps == [5, 10]


def test_poll_slow_down_uses_response_interval():
    sleeps = []
    client = scripted([{"error": "slow_down", "interval": 20}, {"access_token": "t"}])
    poll_for_token("cid", code(), client=client, sleep=sleeps.append)
    assert sleeps == [20]


def test_poll_expired():
    with pytest.raises(GitHubAuthError) as exc:
        poll_for_token(
            "cid", code(), client=scripted([{"error": "expired_token"}]), sleep=lambda s: None
        )
    assert exc.value.reason == "expired"


def test_poll_expires_by_elapsed_time():
    ticks = iter([0.0, 1000.0])
    client = scripted([{"error": "authorization_pending"}])
    with pytest.raises(GitHubAuthError) as exc:
        poll_for_token(
            "cid",
            code(expires_in=900),
            client=client,
            sleep=lambda s: None,
            monotonic=lambda: next(ticks),
        )
    assert exc.value.reason == "expired"


def test_poll_denied():
    with pytest.raises(GitHubAuthError) as exc:
        poll_for_token(
            "cid", code(), client=scripted([{"error": "access_denied"}]), sleep=lambda s: None
        )
    assert exc.value.reason == "denied"


def test_poll_cancelled():
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("polled after cancel")

    with pytest.raises(GitHubAuthError) as exc:
        poll_for_token("cid", code(), client=make_client(handler), cancelled=lambda: True)
    assert exc.value.reason == "cancelled"


def test_poll_unknown_error_is_invalid():
    with pytest.raises(GitHubAuthError) as exc:
        poll_for_token("cid", code(), client=scripted([{"error": "incorrect_client_credentials"}]))
    assert exc.value.reason == "invalid"


def test_refresh_token_discarded():
    client = scripted([{"access_token": "t", "refresh_token": "ghr_x", "expires_in": 10}])
    grant = poll_for_token("cid", code(), client=client, sleep=lambda s: None)
    assert not hasattr(grant, "refresh_token")


def test_validate_pat_rejects_classic():
    for bad in ("ghp_x", "gho_x", ""):
        with pytest.raises(GitHubAuthError) as exc:
            validate_pat(bad)
        assert exc.value.reason == "invalid"
    validate_pat("github_pat_x")
    validate_pat("  github_pat_x\n")


def test_fetch_identity_parses_expiry():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"login": "octo"},
            headers={"github-authentication-token-expiration": "2026-11-17 08:00:00 -0800"},
        )

    login, exp = fetch_identity(PAT, client=make_client(handler))
    assert login == "octo"
    assert seen["auth"] == f"Bearer {PAT}"
    assert seen["url"] == "https://api.github.com/user"
    assert exp is not None and exp.tzinfo is not None
    assert exp == datetime(2026, 11, 17, 16, 0, tzinfo=UTC)


def test_fetch_identity_utc_suffix_and_missing_header():
    def with_utc(request):
        return httpx.Response(
            200,
            json={"login": "o"},
            headers={"github-authentication-token-expiration": "2026-11-17 08:00:00 UTC"},
        )

    _, exp = fetch_identity(PAT, client=make_client(with_utc))
    assert exp == datetime(2026, 11, 17, 8, 0, tzinfo=UTC)

    _, exp = fetch_identity(
        PAT, client=make_client(lambda r: httpx.Response(200, json={"login": "o"}))
    )
    assert exp is None


def test_fetch_identity_401_invalid():
    with pytest.raises(GitHubAuthError) as exc:
        fetch_identity(PAT, client=make_client(lambda r: httpx.Response(401, json={})))
    assert exc.value.reason == "invalid"


def test_network_error_scrubbed():
    def handler(request):
        raise httpx.ConnectError(f"boom while sending {PAT}")

    with pytest.raises(GitHubAuthError) as exc:
        fetch_identity(PAT, client=make_client(handler))
    assert exc.value.reason == "network"
    assert "github_pat_" not in str(exc.value)


def test_device_code_network_error():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(GitHubAuthError) as exc:
        request_device_code("cid", client=make_client(handler))
    assert exc.value.reason == "network"


def _identity_client(expiry: str | None):
    headers = {"github-authentication-token-expiration": expiry} if expiry else {}
    return make_client(lambda r: httpx.Response(200, json={"login": "octo"}, headers=headers))


def test_credential_from_grant_expiry_is_aware():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    cred = credential_from_grant(
        TokenGrant("ghu_tok", 28800), "ron", now=lambda: now, client=_identity_client(None)
    )
    assert cred.kind == "app" and cred.login == "octo" and cred.lightfall_user == "ron"
    assert cred.expires_at == now + timedelta(hours=8)
    assert cred.expires_at.tzinfo is not None
    assert "ghu_tok" not in repr(cred)


def test_credential_from_grant_default_now_is_aware():
    cred = credential_from_grant(TokenGrant("t", 60), "ron", client=_identity_client(None))
    assert cred.expires_at.tzinfo is not None
    assert cred.expires_at > datetime.now(UTC)


def test_credential_from_grant_header_fallback():
    cred = credential_from_grant(
        TokenGrant("t", None), "ron", client=_identity_client("2026-11-17 08:00:00 -0800")
    )
    assert cred.expires_at == datetime(2026, 11, 17, 16, 0, tzinfo=UTC)


def test_credential_from_pat():
    cred = credential_from_pat(PAT, "ron", client=_identity_client("2026-11-17 08:00:00 -0800"))
    assert cred.kind == "pat" and cred.login == "octo"
    assert cred.expires_at.tzinfo is not None
    with pytest.raises(GitHubAuthError):
        credential_from_pat("ghp_x", "ron", client=_identity_client(None))


def test_client_id_falls_back_when_prefs_unavailable(monkeypatch):
    from lightfall.ui.preferences import PreferencesManager

    def boom(*a, **k):
        raise RuntimeError("not initialized")

    monkeypatch.setattr(PreferencesManager, "get_instance", boom)
    assert auth.client_id() == DEFAULT_CLIENT_ID


def test_client_id_uses_preference(monkeypatch):
    from lightfall.ui.preferences import PreferencesManager

    class Fake:
        def get(self, key, default=None):
            assert key == "github_app_client_id"
            return "custom-id"

    monkeypatch.setattr(PreferencesManager, "get_instance", lambda: Fake())
    assert auth.client_id() == "custom-id"
