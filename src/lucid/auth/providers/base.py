"""Abstract base class for authentication providers.

Authentication providers handle the actual authentication flow with
identity services (local database, OIDC/Keycloak, LDAP, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lucid.auth.session import Session


class AuthProvider(ABC):
    """
    Abstract base class for authentication providers.

    Subclasses implement specific authentication mechanisms:
    - LocalAuthProvider: Username/password against local database
    - KeycloakAuthProvider: OIDC authentication via Keycloak

    Example:
        >>> class MyProvider(AuthProvider):
        ...     async def authenticate(self, **kwargs) -> Session | None:
        ...         # Implement authentication logic
        ...         pass
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    @property
    @abstractmethod
    def supports_password_auth(self) -> bool:
        """Whether this provider supports username/password authentication."""
        ...

    @property
    @abstractmethod
    def supports_browser_auth(self) -> bool:
        """Whether this provider supports browser-based authentication (OIDC)."""
        ...

    @abstractmethod
    async def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> Session | None:
        """
        Authenticate a user and create a session.

        Args:
            username: Username for password authentication.
            password: Password for password authentication.
            **kwargs: Additional provider-specific parameters.

        Returns:
            A Session if authentication succeeds, None otherwise.
        """
        ...

    @abstractmethod
    async def logout(self, session: Session) -> None:
        """
        End a session and clean up resources.

        Args:
            session: The session to end.
        """
        ...

    @abstractmethod
    async def refresh(self, session: Session) -> Session | None:
        """
        Refresh an existing session.

        Args:
            session: The session to refresh.

        Returns:
            A new Session with extended expiry, or None if refresh fails.
        """
        ...

    @abstractmethod
    async def check_connectivity(self) -> bool:
        """
        Check if the authentication service is reachable.

        Returns:
            True if the service is available.
        """
        ...

    async def get_user_info(self, session: Session) -> dict[str, Any] | None:
        """
        Get additional user information from the provider.

        Args:
            session: The active session.

        Returns:
            Dictionary of user information, or None if unavailable.
        """
        return None

    async def validate_token(self, token: str) -> bool:
        """
        Validate an authentication token.

        Args:
            token: The token to validate.

        Returns:
            True if the token is valid.
        """
        return False
