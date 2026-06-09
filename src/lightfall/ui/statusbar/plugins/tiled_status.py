"""Tiled status plugin for NCS status bar.

Displays the Tiled data catalog connection state.
"""

from __future__ import annotations

from typing import Any, ClassVar

import qtawesome as qta
from PySide6.QtCore import Slot

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.ui.theme import ThemeManager
from lightfall.utils.logging import logger


class TiledStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing Tiled connection state.

    Displays connection state with theme-aware color coding:
    - success: Connected
    - warning: Connecting
    - error: Error
    - text_secondary: Disabled/Disconnected
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.tiled",
        name="Tiled Status",
        description="Shows Tiled data catalog connection state",
        priority=40,
        position="permanent",
        tooltip="Tiled data catalog status",
    )

    def __init__(self) -> None:
        """Initialize the Tiled status plugin."""
        super().__init__()
        self._service = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "tiled_status"

    def update(self) -> None:
        """Update the button with current Tiled state."""
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()

        try:
            from lightfall.services.tiled_service import TiledConnectionState, TiledService

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
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        self._theme_manager.colors_changed.connect(self.update)

        try:
            from lightfall.services.tiled_service import TiledService

            service = TiledService.get_instance()
            self._service = service
            service.connection_changed.connect(self._on_connection_changed)

        except Exception as e:
            logger.debug("Could not connect to TiledService: {}", e)

    def disconnect_signals(self) -> None:
        """Disconnect from Tiled service signals."""
        if self._service is not None:
            try:
                self._service.connection_changed.disconnect(self._on_connection_changed)
            except RuntimeError:
                pass

        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    @Slot(object, str)
    def _on_connection_changed(self, state, message: str) -> None:
        """Handle connection state change."""
        from lightfall.services.tiled_service import TiledConnectionState

        if state == TiledConnectionState.CONNECTED:
            self._update_display_connected()
        elif state == TiledConnectionState.CONNECTING:
            self._update_display_connecting()
        elif state == TiledConnectionState.ERROR:
            self._update_display_error(message)
        elif state == TiledConnectionState.DISCONNECTED:
            try:
                from lightfall.services.tiled_service import TiledService

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

    def _set_state(self, text: str, color: str, tooltip: str) -> None:
        """Apply text, the database icon (tinted to ``color``), and a tooltip."""
        self.set_icon(qta.icon("mdi6.database", color=color))
        self.set_text(text)
        self.set_color(color)
        self.set_tooltip(tooltip)

    def _update_display_connected(self) -> None:
        # Icon only: the green color already signals "connected".
        self._set_state("", self._colors.success, "Connected to Tiled server")

    def _update_display_connecting(self) -> None:
        self._set_state(
            "Connecting...", self._colors.warning, "Connecting to Tiled server..."
        )

    def _update_display_error(self, message: str = "") -> None:
        tooltip = f"Tiled error: {message}" if message else "Tiled connection error"
        self._set_state("Error", self._colors.error, tooltip)

    def _update_display_disconnected(self) -> None:
        self._set_state(
            "Disconnected", self._colors.text_secondary, "Disconnected from Tiled server"
        )

    def _update_display_disabled(self) -> None:
        self._set_state(
            "Off", self._colors.text_secondary, "Tiled integration is disabled"
        )

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        try:
            from lightfall.services.tiled_service import TiledService

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
