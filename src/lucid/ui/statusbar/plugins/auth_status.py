"""Auth status plugin for NCS status bar.

Displays the current authentication state with color-coded indicator.
"""

from __future__ import annotations

from typing import Any, ClassVar

from lucid.auth.session import AuthState, SessionManager
from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lucid.ui.theme import ThemeManager


class AuthStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing authentication state.

    Displays the auth state with color coding:
    - Green: Authenticated
    - Warning: Offline mode or error
    - Default: Unauthenticated or authenticating
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lucid.statusbar.auth",
        name="Auth Status",
        description="Shows authentication state",
        priority=20,
        position="permanent",
        tooltip="Authentication status",
    )

    STATE_TEXT = {
        AuthState.UNAUTHENTICATED: "Not logged in",
        AuthState.AUTHENTICATING: "Authenticating...",
        AuthState.AUTHENTICATED: "Authenticated",
        AuthState.OFFLINE: "Offline Mode",
        AuthState.ERROR: "Auth Error",
    }

    def __init__(self) -> None:
        """Initialize the auth status plugin."""
        super().__init__()
        self._session_manager: SessionManager | None = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "auth_status"

    def update(self) -> None:
        """Update the button with current auth state."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()

        state = self._session_manager.state
        self.set_text(self.STATE_TEXT.get(state, "Unknown"))

        colors = self._theme_manager.colors
        if state == AuthState.AUTHENTICATED:
            self.set_color(colors.success)
            self.set_tooltip("Successfully authenticated")
        elif state in (AuthState.OFFLINE, AuthState.ERROR):
            self.set_color(colors.warning)
            if state == AuthState.OFFLINE:
                self.set_tooltip("Operating in offline mode with limited features")
            else:
                self.set_tooltip("Authentication error occurred")
        else:
            self.set_color(None)
            if state == AuthState.AUTHENTICATING:
                self.set_tooltip("Authentication in progress...")
            else:
                self.set_tooltip("Not logged in - some features may be restricted")

    def connect_signals(self) -> None:
        """Connect to session manager signals."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()

        self._session_manager.state_changed.connect(self._on_state_changed)
        self._theme_manager.colors_changed.connect(self.update)

    def disconnect_signals(self) -> None:
        """Disconnect from session manager signals."""
        if self._session_manager is not None:
            try:
                self._session_manager.state_changed.disconnect(self._on_state_changed)
            except RuntimeError:
                pass

        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    def _on_state_changed(self, new_state: AuthState, old_state: AuthState) -> None:
        """Handle auth state change signal."""
        self.update()

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        if self._session_manager is not None:
            state = self._session_manager.state
            data["auth_state"] = state.name
            data["auth_state_text"] = self.STATE_TEXT.get(state, "Unknown")

        return data
