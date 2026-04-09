"""Tests for KeycloakTiledAuth with on-demand refresh removed."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from lucid.services.tiled_auth import KeycloakTiledAuth


@pytest.fixture
def auth():
    return KeycloakTiledAuth()


@pytest.fixture
def mock_session_manager():
    """Mock SessionManager singleton with a controllable token."""
    sm = MagicMock()
    sm.session = MagicMock()
    sm.session.token = "token-v1"
    with patch("lucid.services.tiled_auth.SessionManager") as mock_cls:
        mock_cls.get_instance.return_value = sm
        yield sm


class TestSyncAuthFlow:
    """Tests for sync_auth_flow."""

    def test_adds_bearer_token(self, auth, mock_session_manager) -> None:
        """Auth flow should set Authorization header from SessionManager."""
        mock_session_manager.session.token = "my-token"
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        outgoing = next(flow)

        assert outgoing.headers["Authorization"] == "Bearer my-token"

    def test_no_retry_on_success(self, auth, mock_session_manager) -> None:
        """Auth flow should not retry when response is 200."""
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        next(flow)  # yields request

        response = httpx.Response(200, request=request)
        with pytest.raises(StopIteration):
            flow.send(response)

    def test_retries_with_refreshed_token(self, auth, mock_session_manager) -> None:
        """On 401, if SessionManager has a new token, retry with it."""
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        next(flow)  # yields request with token-v1

        # Simulate SessionManager timer refreshing the token
        mock_session_manager.session.token = "token-v2"

        response = httpx.Response(401, request=request)
        retry_request = flow.send(response)

        assert retry_request.headers["Authorization"] == "Bearer token-v2"

    def test_gives_up_when_token_unchanged(self, auth, mock_session_manager) -> None:
        """On 401, if token hasn't changed, don't retry."""
        mock_session_manager.session.token = "stale-token"
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        next(flow)  # yields request

        response = httpx.Response(401, request=request)
        with pytest.raises(StopIteration):
            flow.send(response)

    def test_no_token_sends_unauthenticated(self, auth, mock_session_manager) -> None:
        """With no session token, send request without auth header."""
        mock_session_manager.session = None
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        outgoing = next(flow)

        assert "Authorization" not in outgoing.headers

    def test_does_not_call_keycloak(self, auth, mock_session_manager) -> None:
        """Auth flow must never call Keycloak directly (no refresh_sync)."""
        assert not hasattr(auth, "_refresh_token_sync"), (
            "_refresh_token_sync should be removed"
        )


class TestAsyncAuthFlow:
    """Tests for async_auth_flow."""

    @pytest.mark.asyncio
    async def test_retries_with_refreshed_token(
        self, auth, mock_session_manager
    ) -> None:
        """On 401, if SessionManager has a new token, retry with it."""
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.async_auth_flow(request)
        await flow.__anext__()  # yields request with token-v1

        mock_session_manager.session.token = "token-v2"

        response = httpx.Response(401, request=request)
        retry_request = await flow.asend(response)

        assert retry_request.headers["Authorization"] == "Bearer token-v2"

    @pytest.mark.asyncio
    async def test_gives_up_when_token_unchanged(
        self, auth, mock_session_manager
    ) -> None:
        """On 401, if token hasn't changed, don't retry."""
        mock_session_manager.session.token = "stale-token"
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.async_auth_flow(request)
        await flow.__anext__()

        response = httpx.Response(401, request=request)
        with pytest.raises(StopAsyncIteration):
            await flow.asend(response)
