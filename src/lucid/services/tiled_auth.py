"""Tiled authentication using Keycloak tokens.

Provides httpx.Auth implementations for authenticating Tiled client
requests using tokens from the SessionManager.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from lucid.utils.logging import logger

if TYPE_CHECKING:
    import httpx


class KeycloakTiledAuth:
    """httpx.Auth that uses Keycloak tokens from SessionManager.

    This auth class fetches fresh tokens from the SessionManager for each
    request, ensuring that refreshed tokens are automatically used.

    Example:
        >>> from tiled.client import from_uri
        >>> auth = KeycloakTiledAuth()
        >>> client = from_uri("https://tiled.example.com", auth=auth)
    """

    def __init__(self) -> None:
        """Initialize the auth handler."""
        self._last_token_hash: int | None = None

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Synchronous auth flow for httpx.

        Adds Bearer token from SessionManager to the request.

        Args:
            request: The outgoing request.

        Yields:
            The modified request with Authorization header.
        """
        from lucid.auth.session import SessionManager

        session_manager = SessionManager.get_instance()
        session = session_manager.session

        if session and session.token:
            request.headers["Authorization"] = f"Bearer {session.token}"

            # Log token refresh detection (for debugging)
            token_hash = hash(session.token)
            if self._last_token_hash is not None and token_hash != self._last_token_hash:
                logger.debug("Using refreshed token for Tiled request")
            self._last_token_hash = token_hash
        else:
            logger.debug("No auth token available for Tiled request")

        yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Async auth flow for httpx.

        Adds Bearer token from SessionManager to the request.

        Args:
            request: The outgoing request.

        Yields:
            The modified request with Authorization header.
        """
        from lucid.auth.session import SessionManager

        session_manager = SessionManager.get_instance()
        session = session_manager.session

        if session and session.token:
            request.headers["Authorization"] = f"Bearer {session.token}"

            # Log token refresh detection (for debugging)
            token_hash = hash(session.token)
            if self._last_token_hash is not None and token_hash != self._last_token_hash:
                logger.debug("Using refreshed token for Tiled request")
            self._last_token_hash = token_hash
        else:
            logger.debug("No auth token available for Tiled request")

        yield request
