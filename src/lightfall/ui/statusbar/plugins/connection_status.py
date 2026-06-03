"""Connection status plugin for NCS status bar.

Displays the online/offline connection state.
"""

from __future__ import annotations

from typing import Any, ClassVar

from lightfall.auth.session import SessionManager
from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.ui.theme import ThemeManager


class ConnectionStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing connection state.

    Displays online/offline status with color coding:
    - Green: Online
    - Red: Offline
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.connection",
        name="Connection Status",
        description="Shows online/offline connection state",
        priority=30,
        position="permanent",
        tooltip="Network connection status",
    )

    def __init__(self) -> None:
        """Initialize the connection status plugin."""
        super().__init__()
        self._session_manager: SessionManager | None = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "connection_status"

    def update(self) -> None:
        """Update the button with current connection state."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()

        colors = self._theme_manager.colors
        if self._session_manager.is_offline:
            self.set_text("Offline")
            self.set_color(colors.error)
            self.set_tooltip("Operating in offline mode - network unavailable")
        else:
            self.set_text("Online")
            self.set_color(colors.success)
            self.set_tooltip("Connected to network")

    def connect_signals(self) -> None:
        """Connect to session manager signals."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()

        self._session_manager.offline_mode_changed.connect(self._on_offline_changed)
        self._theme_manager.colors_changed.connect(self.update)

    def disconnect_signals(self) -> None:
        """Disconnect from session manager signals."""
        if self._session_manager is not None:
            try:
                self._session_manager.offline_mode_changed.disconnect(
                    self._on_offline_changed
                )
            except RuntimeError:
                pass

        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    def _on_offline_changed(self, offline: bool) -> None:
        """Handle offline mode change signal."""
        self.update()

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        if self._session_manager is not None:
            data["is_offline"] = self._session_manager.is_offline
            data["connection_state"] = (
                "offline" if self._session_manager.is_offline else "online"
            )

        return data
