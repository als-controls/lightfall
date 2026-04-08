"""Tiled authentication using Keycloak tokens.

Provides httpx.Auth implementations for authenticating Tiled client
requests using tokens from the SessionManager.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator

import httpx

from lucid.utils.logging import logger


class KeycloakTiledAuth(httpx.Auth):
    """httpx.Auth that uses Keycloak tokens from SessionManager.

    This auth class fetches fresh tokens from the SessionManager for each
    request, ensuring that refreshed tokens are automatically used.
    On a 401 response, it attempts a synchronous token refresh and retries
    the request once.

    Example:
        >>> from tiled.client import from_uri
        >>> auth = KeycloakTiledAuth()
        >>> client = from_uri("https://tiled.example.com", auth=auth)
    """

    def __init__(self) -> None:
        """Initialize the auth handler."""
        self._last_token_hash: int | None = None

    def _get_token(self) -> str | None:
        """Get the current access token from SessionManager."""
        from lucid.auth.session import SessionManager

        session = SessionManager.get_instance().session
        return session.token if session else None

    def _refresh_token_sync(self) -> str | None:
        """Synchronously refresh the token and return the new one.

        Uses the provider's sync refresh method (httpx) to avoid reusing
        an aiohttp ClientSession across event loops.

        Returns:
            The new access token, or None if refresh failed.
        """
        from lucid.auth.session import SessionManager

        sm = SessionManager.get_instance()
        if sm.session is None or not sm.session.refresh_token or sm._provider is None:
            return None

        if hasattr(sm._provider, "refresh_sync"):
            new_session = sm._provider.refresh_sync(sm.session)
        else:
            loop = asyncio.new_event_loop()
            try:
                new_session = loop.run_until_complete(
                    sm._provider.refresh(sm.session)
                )
            finally:
                loop.close()

        if new_session:
            sm._session = new_session
            new_token = self._get_token()
            logger.info("Token refreshed on 401 for Tiled request")
            return new_token
        logger.warning("Token refresh failed on 401 for Tiled request")
        return None

    def _set_auth(self, request: httpx.Request, token: str) -> None:
        """Set the Authorization header on a request."""
        request.headers["Authorization"] = f"Bearer {token}"
        token_hash = hash(token)
        if self._last_token_hash is not None and token_hash != self._last_token_hash:
            logger.debug("Using refreshed token for Tiled request")
        self._last_token_hash = token_hash

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Synchronous auth flow for httpx.

        Adds Bearer token from SessionManager to the request.
        If the server responds with 401, refreshes the token and retries once.

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

        # Token was rejected — try to refresh and retry
        logger.debug("Got 401 from Tiled, attempting token refresh")
        new_token = self._refresh_token_sync()
        if new_token and new_token != token:
            self._set_auth(request, new_token)
            yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """Async auth flow for httpx.

        Adds Bearer token from SessionManager to the request.
        If the server responds with 401, refreshes the token and retries once.

        Args:
            request: The outgoing request.

        Yields:
            The modified request with Authorization header.
        """
        from lucid.auth.session import SessionManager

        token = self._get_token()
        if not token:
            logger.debug("No auth token available for Tiled request")
            yield request
            return

        self._set_auth(request, token)
        response = yield request

        if response.status_code != 401:
            return

        # Token was rejected — try to refresh and retry
        logger.debug("Got 401 from Tiled, attempting async token refresh")
        sm = SessionManager.get_instance()
        if sm.session and sm.session.refresh_token:
            ok = await sm.refresh_session()
            if ok:
                new_token = self._get_token()
                if new_token and new_token != token:
                    logger.info("Token refreshed on 401 for Tiled request (async)")
                    self._set_auth(request, new_token)
                    yield request
