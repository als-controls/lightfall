"""User status plugin for NCS status bar.

Displays the current user's display name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtWidgets import QLabel, QWidget

from ncs.auth.session import SessionManager
from ncs.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from ncs.utils.logging import logger

if TYPE_CHECKING:
    pass


class UserStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing the current user.

    Displays the user's display name and updates when the user changes
    (login/logout).

    Example display:
        "User: John Doe"
        "User: Guest"
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="ncs.statusbar.user",
        name="User Status",
        description="Shows the current logged-in user",
        priority=10,
        position="permanent",
        tooltip="Current user",
    )

    def __init__(self) -> None:
        """Initialize the user status plugin."""
        super().__init__()
        self._label: QLabel | None = None
        self._session_manager: SessionManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "user_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the user status label.

        Args:
            parent: Parent widget.

        Returns:
            QLabel showing user name.
        """
        self._label = QLabel(parent)
        self._session_manager = SessionManager.get_instance()
        return self._label

    def update(self) -> None:
        """Update the label with current user."""
        if self._label is None or self._session_manager is None:
            return

        user = self._session_manager.current_user
        self._label.setText(f"User: {user.display_name}")
        self._label.setToolTip(f"Logged in as {user.username}")

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
                # Already disconnected
                pass

    def _on_user_changed(self, user: Any) -> None:
        """Handle user change signal.

        Args:
            user: The new User object.
        """
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
