"""Tiled status plugin for NCS status bar.

Displays the Tiled data catalog connection state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QLabel, QWidget

from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lucid.ui.theme import ThemeManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class TiledStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing Tiled connection state.

    Displays connection state with theme-aware color coding:
    - success: Connected
    - warning: Connecting
    - error: Error
    - text_secondary: Disabled/Disconnected

    Example display:
        "Tiled: Connected" (success)
        "Tiled: Error" (error)
        "Tiled: Off" (muted)
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lucid.statusbar.tiled",
        name="Tiled Status",
        description="Shows Tiled data catalog connection state",
        priority=40,
        position="permanent",
        tooltip="Tiled data catalog status",
    )

    def __init__(self) -> None:
        """Initialize the Tiled status plugin."""
        super().__init__()
        self._label: QLabel | None = None
        self._service = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "tiled_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the Tiled status label.

        Args:
            parent: Parent widget.

        Returns:
            QLabel showing Tiled state.
        """
        self._label = QLabel(parent)
        self._theme_manager = ThemeManager.get_instance()
        return self._label

    def update(self) -> None:
        """Update the label with current Tiled state."""
        if self._label is None:
            return

        try:
            from lucid.services.tiled_service import TiledConnectionState, TiledService

            service = TiledService.get_instance()
            self._service = service

            if not service.config.enabled:
                self._update_display_disabled()
            elif service.state == TiledConnectionState.CONNECTED:
                self._update_display_connected()
            elif service.state == TiledConnectionState.CONNECTING:
                self._update_display_connecting()
            elif service.state == TiledConnectionState.ERROR:
                self._update_display_error(service.error_message)
            else:
                self._update_display_disconnected()

        except Exception as e:
            logger.debug("Could not get TiledService state: {}", e)
            self._update_display_disabled()

    def connect_signals(self) -> None:
        """Connect to Tiled service signals."""
        try:
            from lucid.services.tiled_service import TiledService

            service = TiledService.get_instance()
            self._service = service
            service.connection_changed.connect(self._on_connection_changed)

        except Exception as e:
            logger.debug("Could not connect to TiledService: {}", e)

        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        self._theme_manager.colors_changed.connect(self.update)

    def disconnect_signals(self) -> None:
        """Disconnect from Tiled service signals."""
        if self._service is not None:
            try:
                self._service.connection_changed.disconnect(self._on_connection_changed)
            except RuntimeError:
                # Already disconnected
                pass

        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    @Slot(object, str)
    def _on_connection_changed(self, state, message: str) -> None:
        """Handle connection state change.

        Args:
            state: New TiledConnectionState.
            message: Status message.
        """
        from lucid.services.tiled_service import TiledConnectionState

        if state == TiledConnectionState.CONNECTED:
            self._update_display_connected()
        elif state == TiledConnectionState.CONNECTING:
            self._update_display_connecting()
        elif state == TiledConnectionState.ERROR:
            self._update_display_error(message)
        elif state == TiledConnectionState.DISCONNECTED:
            # Check if disabled or just disconnected
            try:
                from lucid.services.tiled_service import TiledService

                service = TiledService.get_instance()
                if not service.config.enabled:
                    self._update_display_disabled()
                else:
                    self._update_display_disconnected()
            except Exception:
                self._update_display_disconnected()

    @property
    def _colors(self):
        """Resolve theme colors, falling back to the singleton on first use."""
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        return self._theme_manager.colors

    def _update_display_connected(self) -> None:
        """Update display for connected state."""
        if self._label is None:
            return
        self._label.setText("Tiled: Connected")
        self._label.setStyleSheet(f"color: {self._colors.success};")
        self._label.setToolTip("Connected to Tiled server")

    def _update_display_connecting(self) -> None:
        """Update display for connecting state."""
        if self._label is None:
            return
        self._label.setText("Tiled: Connecting...")
        self._label.setStyleSheet(f"color: {self._colors.warning};")
        self._label.setToolTip("Connecting to Tiled server...")

    def _update_display_error(self, message: str = "") -> None:
        """Update display for error state.

        Args:
            message: Error message for tooltip.
        """
        if self._label is None:
            return
        self._label.setText("Tiled: Error")
        self._label.setStyleSheet(f"color: {self._colors.error};")
        tooltip = "Tiled connection error"
        if message:
            tooltip = f"Tiled error: {message}"
        self._label.setToolTip(tooltip)

    def _update_display_disconnected(self) -> None:
        """Update display for disconnected state."""
        if self._label is None:
            return
        self._label.setText("Tiled: Disconnected")
        self._label.setStyleSheet(f"color: {self._colors.text_secondary};")
        self._label.setToolTip("Disconnected from Tiled server")

    def _update_display_disabled(self) -> None:
        """Update display for disabled state."""
        if self._label is None:
            return
        self._label.setText("Tiled: Off")
        self._label.setStyleSheet(f"color: {self._colors.text_secondary};")
        self._label.setToolTip("Tiled integration is disabled")

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        try:
            from lucid.services.tiled_service import TiledService

            service = TiledService.get_instance()
            data["tiled_enabled"] = service.config.enabled
            data["tiled_state"] = service.state.name
            data["tiled_url"] = service.config.url or ""
            if service.error_message:
                data["tiled_error"] = service.error_message

        except Exception:
            data["tiled_enabled"] = False
            data["tiled_state"] = "UNAVAILABLE"

        return data
