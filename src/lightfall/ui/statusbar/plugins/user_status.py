"""User/auth status plugin for NCS status bar.

Displays the current user together with the authentication state, using an
``mdi6.account`` icon variant (and color) to convey the auth state.
"""

from __future__ import annotations

from typing import Any, ClassVar

import qtawesome as qta

from lightfall.auth.session import AuthState, SessionManager
from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.ui.theme import ThemeManager


class UserStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing the current user and authentication state.

    Combines the user's display name with an account icon whose glyph and
    color reflect the auth state:

    - account-check (success): Authenticated
    - account-clock (warning): Authenticating
    - account-alert (warning): Offline mode
    - account-alert (error): Auth error
    - account-cancel (muted): Not logged in / Guest

    Updates on user changes (login/logout) and auth-state transitions.
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.user",
        name="User Status",
        description="Shows the current user and authentication state",
        priority=10,
        position="permanent",
        tooltip="Current user and authentication status",
    )

    # state -> (icon name, color attribute on the theme palette, tooltip)
    STATE_ICON: ClassVar[dict[AuthState, str]] = {
        AuthState.AUTHENTICATED: "mdi6.account-check",
        AuthState.AUTHENTICATING: "mdi6.account-clock",
        AuthState.OFFLINE: "mdi6.account-alert",
        AuthState.ERROR: "mdi6.account-alert",
        AuthState.UNAUTHENTICATED: "mdi6.account-cancel",
    }
    STATE_COLOR: ClassVar[dict[AuthState, str]] = {
        AuthState.AUTHENTICATED: "success",
        AuthState.AUTHENTICATING: "warning",
        AuthState.OFFLINE: "warning",
        AuthState.ERROR: "error",
        AuthState.UNAUTHENTICATED: "text_secondary",
    }
    STATE_TOOLTIP: ClassVar[dict[AuthState, str]] = {
        AuthState.AUTHENTICATING: "Authentication in progress...",
        AuthState.OFFLINE: "Operating in offline mode with limited features",
        AuthState.ERROR: "Authentication error occurred",
        AuthState.UNAUTHENTICATED: "Not logged in - some features may be restricted",
    }

    def __init__(self) -> None:
        """Initialize the user status plugin."""
        super().__init__()
        self._session_manager: SessionManager | None = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "user_status"

    def update(self) -> None:
        """Update the button with the current user and auth state."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()

        state = self._session_manager.state
        user = self._session_manager.current_user
        colors = self._theme_manager.colors

        color = getattr(colors, self.STATE_COLOR.get(state, "text_secondary"))
        icon_name = self.STATE_ICON.get(state, "mdi6.account")

        # Authenticating is transient and has no meaningful user yet; otherwise
        # show the user's display name (e.g. "Guest" when not logged in).
        if state == AuthState.AUTHENTICATING:
            text = "Authenticating..."
        else:
            text = user.display_name

        tooltip = self.STATE_TOOLTIP.get(state)
        if tooltip is None:  # AUTHENTICATED
            tooltip = f"Logged in as {user.username}"

        self.set_icon(qta.icon(icon_name, color=color))
        self.set_text(text)
        self.set_color(color)
        self.set_tooltip(tooltip)

    def connect_signals(self) -> None:
        """Connect to session manager and theme signals."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()

        self._session_manager.user_changed.connect(self._on_user_changed)
        self._session_manager.state_changed.connect(self._on_state_changed)
        self._theme_manager.colors_changed.connect(self.update)

    def disconnect_signals(self) -> None:
        """Disconnect from session manager and theme signals."""
        if self._session_manager is not None:
            try:
                self._session_manager.user_changed.disconnect(self._on_user_changed)
                self._session_manager.state_changed.disconnect(self._on_state_changed)
            except RuntimeError:
                pass

        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    def _on_user_changed(self, user: Any) -> None:
        """Handle user change signal."""
        self.update()

    def _on_state_changed(self, new_state: AuthState, old_state: AuthState) -> None:
        """Handle auth state change signal."""
        self.update()

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        if self._session_manager is not None:
            user = self._session_manager.current_user
            state = self._session_manager.state
            data["current_user"] = {
                "username": user.username,
                "display_name": user.display_name,
                "roles": [r.name for r in user.roles],
            }
            data["auth_state"] = state.name

        return data
