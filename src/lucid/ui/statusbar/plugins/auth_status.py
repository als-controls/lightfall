"""Auth status plugin for NCS status bar.

Displays the current authentication state with color-coded indicator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtWidgets import QLabel, QWidget

from lucid.auth.session import AuthState, SessionManager
from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lucid.ui.theme import ThemeManager

if TYPE_CHECKING:
    pass


class AuthStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing authentication state.

    Displays the auth state with color coding:
    - Green: Authenticated
    - Yellow/warning: Offline mode or error
    - Default: Unauthenticated or authenticating

    Example display:
        "Authenticated" (green)
        "Offline Mode" (yellow)
        "Not logged in"
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lucid.statusbar.auth",
        name="Auth Status",
        description="Shows authentication state",
        priority=20,
        position="permanent",
        tooltip="Authentication status",
    )

    # State display text
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
        self._label: QLabel | None = None
        self._session_manager: SessionManager | None = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "auth_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the auth status label.

        Args:
            parent: Parent widget.

        Returns:
            QLabel showing auth state.
        """
        self._label = QLabel(parent)
        self._session_manager = SessionManager.get_instance()
        self._theme_manager = ThemeManager.get_instance()
        return self._label

    def update(self) -> None:
        """Update the label with current auth state."""
        if self._label is None or self._session_manager is None:
            return

        state = self._session_manager.state
        self._label.setText(self.STATE_TEXT.get(state, "Unknown"))

        # Style based on state
        self._apply_state_style(state)

    def _apply_state_style(self, state: AuthState) -> None:
        """Apply color styling based on auth state.

        Args:
            state: Current authentication state.
        """
        if self._label is None or self._theme_manager is None:
            return

        colors = self._theme_manager.colors

        if state == AuthState.AUTHENTICATED:
            self._label.setStyleSheet(f"color: {colors.success};")
            self._label.setToolTip("Successfully authenticated")
        elif state in (AuthState.OFFLINE, AuthState.ERROR):
            self._label.setStyleSheet(f"color: {colors.warning};")
            if state == AuthState.OFFLINE:
                self._label.setToolTip("Operating in offline mode with limited features")
            else:
                self._label.setToolTip("Authentication error occurred")
        else:
            self._label.setStyleSheet("")
            if state == AuthState.AUTHENTICATING:
                self._label.setToolTip("Authentication in progress...")
            else:
                self._label.setToolTip("Not logged in - some features may be restricted")

    def connect_signals(self) -> None:
        """Connect to session manager signals."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()

        self._session_manager.state_changed.connect(self._on_state_changed)

        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        self._theme_manager.colors_changed.connect(self.update)

    def disconnect_signals(self) -> None:
        """Disconnect from session manager signals."""
        if self._session_manager is not None:
            try:
                self._session_manager.state_changed.disconnect(self._on_state_changed)
            except RuntimeError:
                # Already disconnected
                pass

        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    def _on_state_changed(self, new_state: AuthState, old_state: AuthState) -> None:
        """Handle auth state change signal.

        Args:
            new_state: The new auth state.
            old_state: The previous auth state.
        """
        self.update()

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        if self._session_manager is not None:
            state = self._session_manager.state
            data["auth_state"] = state.name
            data["auth_state_text"] = self.STATE_TEXT.get(state, "Unknown")

        return data
