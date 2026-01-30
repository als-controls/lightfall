"""Local authentication provider for development and testing.

This provider uses a simple in-memory or file-based user database
for authentication without requiring external services.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from lucid.auth.policy import Role
from lucid.auth.providers.base import AuthProvider
from lucid.auth.session import Session, User
from lucid.utils.logging import logger


@dataclass
class LocalUser:
    """A user in the local user database."""

    username: str
    password_hash: str
    display_name: str = ""
    email: str = ""
    roles: list[str] = field(default_factory=lambda: ["user"])
    groups: list[str] = field(default_factory=list)
    enabled: bool = True


def _hash_password(password: str, salt: str = "") -> str:
    """Hash a password with optional salt."""
    to_hash = f"{salt}{password}" if salt else password
    return hashlib.sha256(to_hash.encode()).hexdigest()


class LocalAuthProvider(AuthProvider):
    """
    Development authentication provider with local user database.

    This provider is intended for:
    - Local development without Keycloak
    - Testing authentication flows
    - Demo/training environments

    Users can be defined in a YAML file or added programmatically.

    User file format (users.yaml):
        users:
          admin:
            password_hash: "sha256_hash_here"
            display_name: "Administrator"
            email: "admin@example.com"
            roles: [admin]
          user1:
            password_hash: "sha256_hash_here"
            roles: [user, operator]

    Example:
        >>> provider = LocalAuthProvider()
        >>> provider.add_user("test", "password123", roles=[Role.USER])
        >>> session = await provider.authenticate(username="test", password="password123")
    """

    def __init__(
        self,
        users_file: Path | str | None = None,
        session_duration: timedelta = timedelta(hours=8),
    ) -> None:
        """
        Initialize the local auth provider.

        Args:
            users_file: Optional path to YAML file with user definitions.
            session_duration: How long sessions remain valid.
        """
        self._users: dict[str, LocalUser] = {}
        self._sessions: dict[str, Session] = {}
        self._session_duration = session_duration
        self._salt = secrets.token_hex(16)

        if users_file:
            self._load_users_file(Path(users_file))

        # Always add default development users
        self._add_default_users()

    @property
    def name(self) -> str:
        return "Local Development Auth"

    @property
    def supports_password_auth(self) -> bool:
        return True

    @property
    def supports_browser_auth(self) -> bool:
        return False

    def _load_users_file(self, path: Path) -> None:
        """Load users from YAML file."""
        if not path.exists():
            logger.warning("Users file not found: {}", path)
            return

        try:
            with path.open() as f:
                data = yaml.safe_load(f) or {}

            for username, user_data in data.get("users", {}).items():
                self._users[username] = LocalUser(
                    username=username,
                    password_hash=user_data.get("password_hash", ""),
                    display_name=user_data.get("display_name", username),
                    email=user_data.get("email", ""),
                    roles=user_data.get("roles", ["user"]),
                    groups=user_data.get("groups", []),
                    enabled=user_data.get("enabled", True),
                )
            logger.info("Loaded {} users from {}", len(self._users), path)
        except Exception as e:
            logger.error("Failed to load users file: {}", e)

    def _add_default_users(self) -> None:
        """Add default development users if not already present."""
        defaults = [
            ("admin", "admin", "Administrator", [Role.ADMIN]),
            ("developer", "developer", "Developer", [Role.DEVELOPER]),
            ("scientist", "scientist", "Beamline Scientist", [Role.BEAMLINE_SCIENTIST]),
            ("operator", "operator", "Operator", [Role.OPERATOR]),
            ("user", "user", "Test User", [Role.USER]),
            ("guest", "guest", "Guest User", [Role.GUEST]),
        ]

        for username, password, display_name, roles in defaults:
            if username not in self._users:
                self.add_user(
                    username=username,
                    password=password,
                    display_name=display_name,
                    roles=roles,
                )

    def add_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
        email: str = "",
        roles: list[Role] | None = None,
        groups: list[str] | None = None,
    ) -> None:
        """
        Add a user to the local database.

        Args:
            username: Unique username.
            password: Plain text password (will be hashed).
            display_name: Human-readable name.
            email: Email address.
            roles: List of roles to assign.
            groups: List of group memberships.
        """
        if roles is None:
            roles = [Role.USER]

        self._users[username] = LocalUser(
            username=username,
            password_hash=_hash_password(password, self._salt),
            display_name=display_name or username,
            email=email,
            roles=[r.value for r in roles],
            groups=groups or [],
        )
        logger.debug("Added local user: {}", username)

    def remove_user(self, username: str) -> bool:
        """Remove a user from the local database."""
        if username in self._users:
            del self._users[username]
            return True
        return False

    def _verify_password(self, username: str, password: str) -> bool:
        """Verify a password for a user."""
        user = self._users.get(username)
        if not user or not user.enabled:
            return False
        return user.password_hash == _hash_password(password, self._salt)

    async def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> Session | None:
        """Authenticate a user with username and password."""
        if not username or not password:
            logger.warning("Missing username or password")
            return None

        if not self._verify_password(username, password):
            logger.warning("Authentication failed for user: {}", username)
            return None

        local_user = self._users[username]

        # Map role strings to Role enum
        roles = set()
        for role_str in local_user.roles:
            try:
                roles.add(Role(role_str))
            except ValueError:
                logger.warning("Unknown role: {}", role_str)

        if not roles:
            roles = {Role.GUEST}

        now = datetime.now(UTC)
        user = User(
            username=username,
            display_name=local_user.display_name,
            email=local_user.email,
            roles=roles,
            groups=set(local_user.groups),
            authenticated_at=now,
            expires_at=now + self._session_duration,
        )

        # Generate session token
        token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)

        session = Session(
            user=user,
            token=token,
            refresh_token=refresh_token,
        )

        self._sessions[token] = session
        logger.info("User '{}' authenticated via local provider", username)
        return session

    async def logout(self, session: Session) -> None:
        """End a session."""
        if session.token and session.token in self._sessions:
            del self._sessions[session.token]
            logger.debug("Session ended for user: {}", session.user.username)

    async def refresh(self, session: Session) -> Session | None:
        """Refresh a session's expiry time."""
        if not session.refresh_token:
            return None

        # Verify the session exists
        if session.token not in self._sessions:
            return None

        # Create new session with extended expiry
        now = datetime.now(UTC)
        new_token = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)

        # Update user expiry
        user = User(
            username=session.user.username,
            display_name=session.user.display_name,
            email=session.user.email,
            roles=session.user.roles,
            groups=session.user.groups,
            attributes=session.user.attributes,
            authenticated_at=session.user.authenticated_at,
            expires_at=now + self._session_duration,
        )

        new_session = Session(
            user=user,
            token=new_token,
            refresh_token=new_refresh,
            created_at=session.created_at,
        )

        # Remove old session, add new
        del self._sessions[session.token]
        self._sessions[new_token] = new_session

        return new_session

    async def check_connectivity(self) -> bool:
        """Local provider is always available."""
        return True

    async def get_user_info(self, session: Session) -> dict[str, Any] | None:
        """Get user info from local database."""
        local_user = self._users.get(session.user.username)
        if not local_user:
            return None

        return {
            "username": local_user.username,
            "display_name": local_user.display_name,
            "email": local_user.email,
            "roles": local_user.roles,
            "groups": local_user.groups,
            "enabled": local_user.enabled,
        }

    async def validate_token(self, token: str) -> bool:
        """Check if a token is valid."""
        session = self._sessions.get(token)
        if not session:
            return False
        return session.is_valid
