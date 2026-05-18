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
from lucid.auth.service_key import MintedKey, mint_service_key
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
    id_token: str | None = None
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

        # Token refresh state
        self._refresh_in_progress = False
        self._fast_retry_count = 0
        self._refresh_timer_id: int | None = None

        # Service-key cache (auth-v2): per-(user, service) API keys minted at
        # login. See docs/superpowers/specs/2026-05-17-lucid-auth-v2-design.md.
        # The refresh state above is transitionally kept alive for the logbook
        # bearer flow until lucid-logbook ships its mint endpoint.
        self._service_keys: dict[str, MintedKey] = {}
        self._keys_lock = threading.RLock()

        # Schedule/cancel refresh when auth state changes — this works
        # regardless of whether login() or direct _session assignment is used
        # (LoginDialog sets _session directly without calling login()).
        self.state_changed.connect(self._on_state_for_refresh)

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
                cls._instance._cancel_refresh_timer()
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

    def get_api_key(self, service: str) -> str | None:
        """Return the cached API-key secret for a service, or None if absent or expired.

        Consumers (e.g. ServiceKeyAuth) call this on every request so that a
        re-login that refreshes the cache is picked up immediately.
        """
        with self._keys_lock:
            minted = self._service_keys.get(service)
            if minted is None or minted.is_expired:
                return None
            return minted.secret

    def get_minted_key(self, service: str) -> MintedKey | None:
        """Return the full cached record (for NATS payload embedding), or None if absent or expired.

        Filters expired keys the same way get_api_key does — NATS dispatchers
        that embed an expired key would just hand the executor a dead
        credential. Callers that genuinely need to inspect expired keys can
        read the cache directly.
        """
        with self._keys_lock:
            minted = self._service_keys.get(service)
            if minted is not None and minted.is_expired:
                return None
            return minted

    # Default scopes per service. See spec §"Scopes".
    _SERVICE_SCOPES: dict[str, list[str]] = {
        "tiled": [
            "read:metadata", "read:data",
            "write:metadata", "write:data",
            "register", "create:node",
        ],
        "logbook": [],  # logbook has no granular scope model
    }

    _SESSION_KEY_LIFETIME = 604800  # 7 days, per spec

    def _mint_all_service_keys(self, bearer_token: str) -> None:
        """Mint a session key per configured service in sequence.

        Called by login() once authentication succeeds. Failures are logged,
        never raised — login degrades but does not fail per spec.

        Mints session keys for Tiled and Logbook. Each service mint is
        independent: a failure on one service is logged and skipped, leaving
        the other service's cached key intact.

        Synchronous httpx + small N, so serial execution keeps the code
        simple. If N grows beyond a handful of services or any mint ever
        blocks for noticeable wall time, switch to a thread pool here.
        """
        from lucid.logbook.url import get_logbook_base_url
        from lucid.services.tiled_service import get_tiled_base_url

        urls = {
            "tiled": get_tiled_base_url().rstrip("/") + "/api/v1",
            "logbook": get_logbook_base_url().rstrip("/") + "/api/v1",
        }

        hostname = self._hostname_for_note()
        sub = (
            self._session.user.attributes.get("sub", "unknown")
            if self._session and self._session.user
            else "unknown"
        )
        note = f"lucid {hostname} {sub}"

        for service, url in urls.items():
            scopes = self._SERVICE_SCOPES.get(service, [])
            try:
                minted = mint_service_key(
                    url,
                    bearer_token,
                    expires_in=self._SESSION_KEY_LIFETIME,
                    scopes=scopes,
                    note=note,
                )
            except Exception as e:
                logger.warning(
                    "mint failed for service={} url={}: {}", service, url, e
                )
                continue

            with self._keys_lock:
                self._service_keys[service] = minted
            logger.info("session key cached for service={}", service)

    @staticmethod
    def _hostname_for_note() -> str:
        """Return the local hostname for mint audit notes; falls back gracefully."""
        import socket
        try:
            return socket.gethostname()
        except Exception:
            return "unknown-host"

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

                # Mint per-service API keys before transitioning to AUTHENTICATED
                # so the keys are available to any AUTHENTICATED-state listeners.
                # Failure is non-fatal: individual slots may be empty.
                if session.token:
                    import asyncio
                    await asyncio.to_thread(
                        self._mint_all_service_keys, session.token
                    )

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
        with self._keys_lock:
            self._service_keys.clear()
        self._set_state(AuthState.UNAUTHENTICATED)
        self.user_changed.emit(ANONYMOUS_USER)
        logger.info("User '{}' logged out", old_user.username)

        # Purge synced logbook data so the local DB stays lean
        try:
            from lucid.logbook.client import LogbookClient
            client = LogbookClient.get_instance()
            if client._initialized:
                client.purge_synced()
        except Exception as exc:
            logger.debug("Logbook purge on logout skipped: {}", exc)

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

    @staticmethod
    def _get_jwt_exp(token: str) -> datetime | None:
        """Extract the expiry time from a JWT access token.

        The LoginDialog overwrites user.expires_at with an app-level session
        duration (default 2 hours), which is unrelated to the actual Keycloak
        token lifetime (~5 minutes). This method reads the real ``exp`` claim
        so the refresh timer fires before the token actually expires.
        """
        import base64
        import json

        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            payload = parts[1]
            payload += "=" * (4 - len(payload) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(payload))
            exp = decoded.get("exp")
            if exp:
                return datetime.fromtimestamp(exp, tz=UTC)
        except Exception:
            pass
        return None

    def _schedule_refresh(self) -> None:
        """Schedule a one-shot timer to refresh the token before it expires.

        Uses the JWT ``exp`` claim from the access token rather than
        ``user.expires_at``, which may be overwritten with an app-level
        session duration.
        """
        self._cancel_refresh_timer()

        if self._session is None or not self._session.token:
            return

        expires_at = self._get_jwt_exp(self._session.token)
        if expires_at is None:
            logger.warning("Cannot read exp from access token, refresh not scheduled")
            return

        now = datetime.now(UTC)
        delay_s = max(0, (expires_at - now).total_seconds() - 60)
        delay_ms = int(delay_s * 1000)

        logger.info(
            "Token refresh scheduled in {}s (JWT exp={})",
            int(delay_s),
            expires_at.isoformat(),
        )
        self._start_single_shot(delay_ms)

    def _on_state_for_refresh(self, new_state: AuthState, old_state: AuthState) -> None:
        """Schedule or cancel token refresh when auth state changes."""
        if new_state == AuthState.AUTHENTICATED:
            self._schedule_refresh()
        elif old_state == AuthState.AUTHENTICATED:
            self._cancel_refresh_timer()
            self._refresh_in_progress = False
            self._fast_retry_count = 0

    def _start_single_shot(self, delay_ms: int) -> None:
        """Start a single-shot timer. Separated for testability."""
        timer_id = self.startTimer(delay_ms)
        if timer_id == 0:
            logger.error("startTimer failed (no event loop?), refresh will not be scheduled")
            return
        self._refresh_timer_id = timer_id

    def _cancel_refresh_timer(self) -> None:
        """Cancel the pending refresh timer if any."""
        if self._refresh_timer_id is not None:
            self.killTimer(self._refresh_timer_id)
            self._refresh_timer_id = None

    def timerEvent(self, event) -> None:  # noqa: N802
        """Handle QObject timer events (used for single-shot refresh)."""
        if event.timerId() == self._refresh_timer_id:
            self.killTimer(self._refresh_timer_id)
            self._refresh_timer_id = None
            self._do_scheduled_refresh()
        else:
            super().timerEvent(event)

    def _do_scheduled_refresh(self) -> None:
        """Execute the scheduled token refresh.

        Runs the actual Keycloak call in a background thread via QThreadFuture.
        Guarded by _refresh_in_progress to prevent concurrent refresh attempts.
        """
        if self._refresh_in_progress:
            logger.debug("Token refresh already in progress, skipping")
            return

        if self._session is None or self._provider is None:
            return

        if not self._session.refresh_token:
            logger.warning("No refresh token available, cannot refresh")
            return

        self._refresh_in_progress = True

        from lucid.utils.threads import QThreadFuture

        # Capture references for the background thread
        session = self._session
        provider = self._provider

        def _refresh():
            has_refresh_token = bool(session.refresh_token)
            logger.debug(
                "Token refresh starting: has_refresh_token={}, provider={}",
                has_refresh_token,
                type(provider).__name__,
            )
            if hasattr(provider, "refresh_sync"):
                result = provider.refresh_sync(session)
            else:
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        provider.refresh(session)
                    )
                finally:
                    loop.close()
            if result is None:
                raise RuntimeError(
                    f"Provider returned None from refresh "
                    f"(has_refresh_token={has_refresh_token})"
                )
            return result

        QThreadFuture(
            _refresh,
            callback_slot=self._on_refresh_success,
            except_slot=self._on_refresh_failure,
            key="session-token-refresh",
            name="session-token-refresh",
        ).start()

    def _on_refresh_success(self, new_session: Session | None = None) -> None:
        """Handle successful token refresh (called on main thread).

        Verifies the new session has a later expiry, updates state, and
        schedules the next refresh.

        Note: QThreadFuture's generator-based callback emits sigResult twice —
        once with the real value and once with None when the generator ends.
        The None call is ignored here; actual provider failures raise in
        _refresh() and go to _on_refresh_failure via except_slot.
        """
        if new_session is None:
            return

        self._refresh_in_progress = False

        # Verify the refresh actually advanced the JWT expiry
        old_expires = (
            self._get_jwt_exp(self._session.token)
            if self._session and self._session.token
            else None
        )
        new_expires = (
            self._get_jwt_exp(new_session.token)
            if new_session.token
            else None
        )
        if old_expires and new_expires and new_expires <= old_expires:
            self._on_refresh_failure(
                RuntimeError(
                    f"Refresh did not advance expiry: "
                    f"{old_expires.isoformat()} -> {new_expires.isoformat()}"
                )
            )
            return

        self._session = new_session
        self._fast_retry_count = 0

        logger.info("Token refresh OK for '{}'", new_session.user.username)
        self._schedule_refresh()

    def _on_refresh_failure(self, exc: Exception) -> None:
        """Handle failed token refresh (called on main thread).

        Retries quickly up to 3 times, then falls back to 30s intervals.
        """
        self._refresh_in_progress = False
        self._fast_retry_count += 1

        if self._fast_retry_count <= 3:
            delay_ms = 3000
            logger.warning(
                "Token refresh failed (attempt {}): {}, retrying in {}ms",
                self._fast_retry_count,
                exc,
                delay_ms,
            )
        else:
            delay_ms = 30_000
            logger.warning(
                "Token refresh failed (attempt {}): {}, backing off to {}s",
                self._fast_retry_count,
                exc,
                delay_ms // 1000,
            )

        self._start_single_shot(delay_ms)

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

    def _attempt_reconnect(self) -> None:
        """Attempt to reconnect to authentication service.

        PySide6 QTimer cannot await async slots, so we run the async
        reconnection logic via QThreadFuture with its own event loop.
        """
        if self._provider is None:
            return

        import asyncio

        from lucid.utils.threads import QThreadFuture

        async def _reconnect():
            if await self._provider.check_connectivity():
                return True
            return False

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(_reconnect())
            finally:
                loop.close()

        def _on_done(connected):
            if not connected:
                return
            logger.info("Authentication service reconnected")
            self._set_offline_mode(False)

            # Try to restore session if we have one
            if self._session and self._session.refresh_token:
                self._do_scheduled_refresh()
            else:
                self._set_state(AuthState.UNAUTHENTICATED)

        def _on_error(exc):
            pass  # Still offline

        QThreadFuture(
            _run,
            callback_slot=_on_done,
            except_slot=_on_error,
            key="session-reconnect",
            name="session-reconnect",
        ).start()

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
