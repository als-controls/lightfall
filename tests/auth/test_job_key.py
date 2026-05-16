"""Tests for lucid.auth.job_key.mint_job_key()."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import httpx
import pytest

from lucid.auth.job_key import MintedJobKey, mint_job_key, revoke_job_key


@pytest.fixture
def mock_httpx_post():
    """Patch httpx.post to return a canned Tiled apikey response."""
    with patch("lucid.auth.job_key.httpx.post") as mock:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "secret": "ab12cd34ef56" + "0" * 52,        # 64-hex chars
            "first_eight": "ab12cd34",
            "expiration_time": "2026-05-17T20:14:00Z",
            "scopes": ["read:metadata", "read:data", "write:metadata", "write:data"],
            "note": "lucid pipeline reduce_saxs",
        }
        response.raise_for_status.return_value = None
        mock.return_value = response
        yield mock


def test_mint_job_key_returns_secret_and_expiry(mock_httpx_post):
    result = mint_job_key(
        tiled_url="https://tiled.test/api/v1",
        bearer_token="fake-keycloak-token",
        lifetime=86400,
        scopes=["read:metadata", "read:data", "write:metadata", "write:data"],
        note="lucid pipeline reduce_saxs",
    )
    assert isinstance(result, MintedJobKey)
    assert result.secret.startswith("ab12cd34")
    assert result.first_eight == "ab12cd34"
    assert result.expires_at == "2026-05-17T20:14:00Z"


def test_mint_job_key_posts_to_correct_url(mock_httpx_post):
    mint_job_key(
        tiled_url="https://tiled.test/api/v1",
        bearer_token="fake-keycloak-token",
        lifetime=3600,
        scopes=["read:metadata"],
        note="t",
    )
    args, kwargs = mock_httpx_post.call_args
    assert args[0] == "https://tiled.test/api/v1/auth/apikey"
    assert kwargs["headers"]["Authorization"] == "Bearer fake-keycloak-token"
    assert kwargs["json"]["expires_in"] == 3600
    assert kwargs["json"]["scopes"] == ["read:metadata"]


def test_revoke_calls_delete():
    with patch("lucid.auth.job_key.httpx.delete") as mock_del:
        resp = MagicMock(status_code=200)
        resp.raise_for_status.return_value = None
        mock_del.return_value = resp
        revoke_job_key("https://tiled.test/api/v1", "bearer-tok", first_eight="ab12cd34")
        args, kwargs = mock_del.call_args
        assert args[0] == "https://tiled.test/api/v1/auth/apikey"
        assert kwargs["params"] == {"first_eight": "ab12cd34"}
        assert kwargs["headers"]["Authorization"] == "Bearer bearer-tok"


def test_revoke_swallows_errors_with_warning():
    """revoke_job_key is best-effort: transient errors must not propagate
    (so it's safe in `finally:` blocks)."""
    with patch("lucid.auth.job_key.httpx.delete") as mock_del:
        mock_del.side_effect = httpx.ConnectError("network down")
        # Must not raise.
        revoke_job_key("https://tiled.test/api/v1", "tok", first_eight="ab12cd34")
