"""User status plugin for NCS status bar.

Displays the current user's display name.
"""

from __future__ import annotations

from typing import Any, ClassVar

from lucid.auth.session import SessionManager
from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata


class UserStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing the current user.

    Displays the user's display name and updates when the user changes
    (login/logout).

    Example display:
        "User: John Doe"
        "User: Guest"
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lucid.statusbar.user",
        name="User Status",
        description="Shows the current logged-in user",
        priority=10,
        position="permanent",
        tooltip="Current user",
    )

    def __init__(self) -> None:
        """Initialize the user status plugin."""
        super().__init__()
        self._session_manager: SessionManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "user_status"

    def update(self) -> None:
        """Update the button with current user."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()

        user = self._session_manager.current_user
        self.set_text(f"User: {user.display_name}")
        self.set_tooltip(f"Logged in as {user.username}")

    def connect_signals(self) -> None:
        """Connect to session manager signals."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()

        self._session_manager.user_changed.connect(self._on_user_changed)

    def disconnect_signals(self) -> None:
        """Disconnect from session manager signals."""
        if self._session_manager is not None:
            try:
                self._session_manager.user_changed.disconnect(self._on_user_changed)
            except RuntimeError:
                pass

    def _on_user_changed(self, user: Any) -> None:
        """Handle user change signal."""
        self.update()

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        if self._session_manager is not None:
            user = self._session_manager.current_user
            data["current_user"] = {
                "username": user.username,
                "display_name": user.display_name,
                "roles": [r.name for r in user.roles],
            }

        return data
