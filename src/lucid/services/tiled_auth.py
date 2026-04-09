"""Tiled authentication using Keycloak tokens.

Provides httpx.Auth implementations for authenticating Tiled client
requests using tokens from the SessionManager.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

import httpx

from lucid.auth.session import SessionManager
from lucid.utils.logging import logger


class KeycloakTiledAuth(httpx.Auth):
    """httpx.Auth that uses Keycloak tokens from SessionManager.

    This auth class fetches the current token from the SessionManager for each
    request. It never calls Keycloak directly — token refresh is handled
    exclusively by SessionManager's scheduled timer.

    On a 401 response, it checks whether SessionManager has since refreshed
    the token and retries once if so.

    Example:
        >>> from tiled.client import from_uri
        >>> auth = KeycloakTiledAuth()
        >>> client = from_uri("https://tiled.example.com", auth=auth)
    """

    def _get_token(self) -> str | None:
        """Get the current access token from SessionManager."""
        session = SessionManager.get_instance().session
        return session.token if session else None

    @staticmethod
    def _set_auth(request: httpx.Request, token: str) -> None:
        """Set the Authorization header on a request."""
        request.headers["Authorization"] = f"Bearer {token}"

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Synchronous auth flow for httpx.

        Adds Bearer token from SessionManager to the request.
        If the server responds with 401, checks if SessionManager has a
        newer token and retries once if so.

        Args:
            request: The outgoing request.

        Yields:
            The modified request with Authorization header.
        """
        token = self._get_token()
        if not token:
            logger.debug("No auth token available for Tiled request")
            yield request
            return

        self._set_auth(request, token)
        response = yield request

        if response.status_code != 401:
            return

        # Token was rejected. Check if SessionManager already refreshed it.
        current_token = self._get_token()
        if current_token and current_token != token:
            logger.debug("Using refreshed token for Tiled retry")
            self._set_auth(request, current_token)
            yield request
        # Otherwise: give up. The timer will refresh soon.

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """Async auth flow for httpx.

        Adds Bearer token from SessionManager to the request.
        If the server responds with 401, checks if SessionManager has a
        newer token and retries once if so.

        Args:
            request: The outgoing request.

        Yields:
            The modified request with Authorization header.
        """
        token = self._get_token()
        if not token:
            logger.debug("No auth token available for Tiled request")
            yield request
            return

        self._set_auth(request, token)
        response = yield request

        if response.status_code != 401:
            return

        # Token was rejected. Check if SessionManager already refreshed it.
        current_token = self._get_token()
        if current_token and current_token != token:
            logger.debug("Using refreshed token for Tiled retry (async)")
            self._set_auth(request, current_token)
            yield request
