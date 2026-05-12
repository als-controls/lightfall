"""Connection status plugin for NCS status bar.

Displays the online/offline connection state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtWidgets import QLabel, QWidget

from lucid.auth.session import SessionManager
from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lucid.ui.theme import ThemeManager

if TYPE_CHECKING:
    pass


class ConnectionStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing connection state.

    Displays online/offline status with color coding:
    - Green: Online
    - Red: Offline

    Example display:
        "Online" (green)
        "Offline" (red)
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lucid.statusbar.connection",
        name="Connection Status",
        description="Shows online/offline connection state",
        priority=30,
        position="permanent",
        tooltip="Network connection status",
    )

    def __init__(self) -> None:
        """Initialize the connection status plugin."""
        super().__init__()
        self._label: QLabel | None = None
        self._session_manager: SessionManager | None = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "connection_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the connection status label.

        Args:
            parent: Parent widget.

        Returns:
            QLabel showing connection state.
        """
        self._label = QLabel(parent)
        self._session_manager = SessionManager.get_instance()
        self._theme_manager = ThemeManager.get_instance()
        return self._label

    def update(self) -> None:
        """Update the label with current connection state."""
        if self._label is None or self._session_manager is None:
            return

        is_offline = self._session_manager.is_offline
        self._apply_connection_style(is_offline)

    def _apply_connection_style(self, is_offline: bool) -> None:
        """Apply styling based on connection state.

        Args:
            is_offline: True if in offline mode.
        """
        if self._label is None or self._theme_manager is None:
            return

        colors = self._theme_manager.colors

        if is_offline:
            self._label.setText("Offline")
            self._label.setStyleSheet(f"color: {colors.error};")
            self._label.setToolTip("Operating in offline mode - network unavailable")
        else:
            self._label.setText("Online")
            self._label.setStyleSheet(f"color: {colors.success};")
            self._label.setToolTip("Connected to network")

    def connect_signals(self) -> None:
        """Connect to session manager signals."""
        if self._session_manager is None:
            self._session_manager = SessionManager.get_instance()

        self._session_manager.offline_mode_changed.connect(self._on_offline_changed)

        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        self._theme_manager.colors_changed.connect(self.update)

    def disconnect_signals(self) -> None:
        """Disconnect from session manager signals."""
        if self._session_manager is not None:
            try:
                self._session_manager.offline_mode_changed.disconnect(
                    self._on_offline_changed
                )
            except RuntimeError:
                # Already disconnected
                pass

        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    def _on_offline_changed(self, offline: bool) -> None:
        """Handle offline mode change signal.

        Args:
            offline: True if now in offline mode.
        """
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
