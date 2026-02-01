"""Session management and user identity for NCS.

This module provides:
- User identity representation
- Session lifecycle management
- Offline mode handling
- Authentication state tracking
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal

from lucid.auth.policy import Permission, PolicyEngine, Role
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.auth.providers.base import AuthProvider


class AuthState(Enum):
    """Authentication state machine states."""

    UNAUTHENTICATED = auto()  # No user logged in
    AUTHENTICATING = auto()  # Auth in progress
    AUTHENTICATED = auto()  # Successfully authenticated
    OFFLINE = auto()  # Network unavailable, restricted mode
    ERROR = auto()  # Authentication error occurred


@dataclass
class User:
    """Represents an authenticated user.

    Attributes:
        username: Unique user identifier.
        display_name: Human-readable name.
        email: User email address.
        roles: Set of assigned roles.
        groups: Set of group memberships (from identity provider).
        attributes: Additional user attributes from the identity provider.
        authenticated_at: When authentication occurred.
        expires_at: When the session expires.
    """

    username: str
    display_name: str = ""
    email: str = ""
    roles: set[Role] = field(default_factory=lambda: {Role.GUEST})
    groups: set[str] = field(default_factory=set)
    attributes: dict[str, Any] = field(default_factory=dict)
    authenticated_at: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        """Set default display name if not provided."""
        if not self.display_name:
            self.display_name = self.username

    @property
    def is_expired(self) -> bool:
        """Check if the session has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at

    @property
    def highest_role(self) -> Role:
        """Get the user's highest privilege role."""
        # Role enum values are ordered by privilege
        role_order = [
            Role.DEVELOPER,
            Role.ADMIN,
            Role.STAFF,
            Role.OPERATOR,
            Role.USER,
            Role.GUEST,
        ]
        for role in role_order:
            if role in self.roles:
                return role
        return Role.GUEST

    def has_role(self, role: Role) -> bool:
        """Check if user has a specific role."""
        return role in self.roles


# Anonymous/guest user singleton
ANONYMOUS_USER = User(
    username="anonymous",
    display_name="Guest",
    roles={Role.GUEST},
)


@dataclass
class Session:
    """Represents an active user session.

    Attributes:
        user: The authenticated user.
        token: Authentication token (if applicable).
        refresh_token: Token for refreshing authentication.
        created_at: Session creation time.
        last_activity: Last user activity time.
        metadata: Additional session data.
    """

    user: User
    token: str | None = None
    refresh_token: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def update_activity(self) -> None:
        """Update the last activity timestamp."""
        self.last_activity = datetime.now(UTC)

    @property
    def is_valid(self) -> bool:
        """Check if the session is still valid."""
        return not self.user.is_expired


class SessionManager(QObject):
    """
    Manages user sessions and authentication state.

    SessionManager coordinates:
    - Authentication flow with providers
    - Session lifecycle (create, refresh, destroy)
    - Offline mode handling
    - Permission checking via PolicyEngine

    Signals:
        state_changed: Emitted when auth state changes (new_state, old_state).
        user_changed: Emitted when user changes (new_user).
        session_expiring: Emitted when session is about to expire (seconds_remaining).
        offline_mode_changed: Emitted when offline mode changes (is_offline).

    Example:
        >>> manager = SessionManager()
        >>> manager.set_provider(LocalAuthProvider())
        >>> await manager.login("user", "pass")
        >>> manager.check_permission(Permission.DEVICE_CONTROL)
        True
    """

    state_changed = Signal(AuthState, AuthState)  # new, old
    user_changed = Signal(User)
    session_expiring = Signal(int)  # seconds remaining
    offline_mode_changed = Signal(bool)

    _instance: SessionManager | None = None
    _lock = threading.RLock()

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the session manager."""
        super().__init__(parent)
        self._state = AuthState.UNAUTHENTICATED
        self._session: Session | None = None
        self._provider: AuthProvider | None = None
        self._policy_engine = PolicyEngine()
        self._offline_mode = False

        # Session expiry timer
        self._expiry_timer = QTimer(self)
        self._expiry_timer.timeout.connect(self._check_session_expiry)
        self._expiry_timer.start(60000)  # Check every minute

        # Reconnection timer for offline mode
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect)

    @classmethod
    def get_instance(cls) -> SessionManager:
        """Get the singleton SessionManager instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance._expiry_timer.stop()
                cls._instance._reconnect_timer.stop()
                cls._instance.deleteLater()
            cls._instance = None

    @property
    def state(self) -> AuthState:
        """Current authentication state."""
        return self._state

    @property
    def session(self) -> Session | None:
        """Current session, if any."""
        return self._session

    @property
    def current_user(self) -> User:
        """Current user (anonymous if not authenticated)."""
        if self._session and self._session.is_valid:
            return self._session.user
        return ANONYMOUS_USER

    @property
    def is_authenticated(self) -> bool:
        """Check if a user is currently authenticated."""
        return self._state == AuthState.AUTHENTICATED and self._session is not None

    @property
    def is_offline(self) -> bool:
        """Check if in offline mode."""
        return self._offline_mode

    @property
    def policy_engine(self) -> PolicyEngine:
        """Access the policy engine for permission checks."""
        return self._policy_engine

    def _set_state(self, new_state: AuthState) -> None:
        """Update authentication state and emit signal."""
        if new_state != self._state:
            old_state = self._state
            self._state = new_state
            logger.info("Auth state: {} -> {}", old_state.name, new_state.name)
            self.state_changed.emit(new_state, old_state)

    def set_provider(self, provider: AuthProvider) -> None:
        """Set the authentication provider.

        Args:
            provider: The authentication provider to use.
        """
        self._provider = provider
        logger.info("Auth provider set: {}", provider.__class__.__name__)

    async def login(
        self,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> bool:
        """Authenticate a user.

        Args:
            username: Username for password auth.
            password: Password for password auth.
            **kwargs: Additional auth parameters for the provider.

        Returns:
            True if authentication succeeded.
        """
        if self._provider is None:
            logger.error("No authentication provider configured")
            self._set_state(AuthState.ERROR)
            return False

        self._set_state(AuthState.AUTHENTICATING)

        try:
            session = await self._provider.authenticate(
                username=username,
                password=password,
                **kwargs,
            )

            if session:
                self._session = session
                self._set_state(AuthState.AUTHENTICATED)
                self.user_changed.emit(session.user)
                logger.info("User '{}' authenticated", session.user.username)

                # Exit offline mode if we were in it
                if self._offline_mode:
                    self._set_offline_mode(False)

                return True
            else:
                self._set_state(AuthState.UNAUTHENTICATED)
                return False

        except Exception as e:
            logger.error("Authentication failed: {}", e)
            self._set_state(AuthState.ERROR)
            return False

    async def logout(self) -> None:
        """End the current session."""
        if self._session is None:
            return

        if self._provider:
            try:
                await self._provider.logout(self._session)
            except Exception as e:
                logger.warning("Logout cleanup failed: {}", e)

        old_user = self._session.user
        self._session = None
        self._set_state(AuthState.UNAUTHENTICATED)
        self.user_changed.emit(ANONYMOUS_USER)
        logger.info("User '{}' logged out", old_user.username)

    async def refresh_session(self) -> bool:
        """Attempt to refresh the current session.

        Returns:
            True if refresh succeeded.
        """
        if self._session is None or self._provider is None:
            return False

        try:
            new_session = await self._provider.refresh(self._session)
            if new_session:
                self._session = new_session
                logger.debug("Session refreshed for '{}'", new_session.user.username)
                return True
        except Exception as e:
            logger.warning("Session refresh failed: {}", e)

        return False

    def _check_session_expiry(self) -> None:
        """Check if session is expiring soon."""
        if self._session is None or self._session.user.expires_at is None:
            return

        now = datetime.now(UTC)
        expires_at = self._session.user.expires_at
        remaining = (expires_at - now).total_seconds()

        if remaining <= 0:
            # Session expired
            logger.warning("Session expired")
            self._session = None
            self._set_state(AuthState.UNAUTHENTICATED)
            self.user_changed.emit(ANONYMOUS_USER)
        elif remaining <= 300:  # 5 minutes warning
            self.session_expiring.emit(int(remaining))

    def enter_offline_mode(self) -> None:
        """Enter offline mode due to network unavailability."""
        if self._offline_mode:
            return

        self._set_offline_mode(True)
        self._set_state(AuthState.OFFLINE)

        # Start reconnection attempts
        self._reconnect_timer.start(30000)  # Try every 30 seconds

    def _set_offline_mode(self, offline: bool) -> None:
        """Set offline mode state."""
        if offline != self._offline_mode:
            self._offline_mode = offline
            self.offline_mode_changed.emit(offline)
            logger.info("Offline mode: {}", "enabled" if offline else "disabled")

            if not offline:
                self._reconnect_timer.stop()

    async def _attempt_reconnect(self) -> None:
        """Attempt to reconnect to authentication service."""
        if self._provider is None:
            return

        try:
            if await self._provider.check_connectivity():
                logger.info("Authentication service reconnected")
                self._set_offline_mode(False)

                # Try to restore session if we have one
                if self._session and self._session.refresh_token:
                    if await self.refresh_session():
                        self._set_state(AuthState.AUTHENTICATED)
                    else:
                        self._set_state(AuthState.UNAUTHENTICATED)
                else:
                    self._set_state(AuthState.UNAUTHENTICATED)
        except Exception:
            pass  # Still offline

    # Permission checking shortcuts

    def check_permission(
        self,
        permission: Permission,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Check if current user has a permission.

        In offline mode, only view permissions are allowed.

        Args:
            permission: The permission to check.
            context: Optional ABAC context.

        Returns:
            True if permission is granted.
        """
        if self._offline_mode:
            # In offline mode, only allow view permissions
            view_permissions = {
                Permission.DEVICE_VIEW,
                Permission.SCAN_VIEW,
                Permission.DATA_VIEW,
                Permission.CONFIG_VIEW,
                Permission.PANEL_VIEW_BASIC,
                Permission.LOGBOOK_VIEW,
            }
            if permission not in view_permissions:
                return False

        return self._policy_engine.check_permission(
            self.current_user,
            permission,
            context,
        )

    def require_permission(
        self,
        permission: Permission,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Require a permission, raising if not granted.

        Args:
            permission: The permission required.
            context: Optional ABAC context.

        Raises:
            PermissionError: If permission is not granted.
        """
        if self._offline_mode:
            raise PermissionError("Operation not permitted in offline mode")

        self._policy_engine.require_permission(
            self.current_user,
            permission,
            context,
        )

    def get_user_permissions(self) -> set[Permission]:
        """Get all permissions for the current user."""
        if self._offline_mode:
            # Return only view permissions in offline mode
            return {
                Permission.DEVICE_VIEW,
                Permission.SCAN_VIEW,
                Permission.DATA_VIEW,
                Permission.CONFIG_VIEW,
                Permission.PANEL_VIEW_BASIC,
                Permission.LOGBOOK_VIEW,
            }
        return self._policy_engine.get_user_permissions(self.current_user)

    def update_activity(self) -> None:
        """Update session activity timestamp."""
        if self._session:
            self._session.update_activity()
