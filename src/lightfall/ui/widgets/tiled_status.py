"""Tiled status widget for NCS status bar.

Displays the current Tiled connection state with color-coded indicators.
"""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QLabel, QWidget

from lightfall.utils.logging import logger


class TiledStatusWidget(QLabel):
    """Status bar widget showing Tiled connection state.

    Displays connection state with color coding:
    - Green: Connected
    - Yellow: Connecting
    - Red: Error
    - Gray: Disabled/Disconnected

    The widget automatically subscribes to TiledService connection
    changes and updates its display accordingly.

    Example:
        >>> widget = TiledStatusWidget()
        >>> statusbar.addPermanentWidget(widget)
    """

    # State colors
    COLOR_CONNECTED = "#4CAF50"  # Green
    COLOR_CONNECTING = "#FFC107"  # Yellow/Amber
    COLOR_ERROR = "#F44336"  # Red
    COLOR_DISABLED = "#9E9E9E"  # Gray

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Tiled status widget.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)

        # Initial state
        self._update_display_disabled()

        # Subscribe to TiledService changes
        self._subscribe_to_service()

    def _subscribe_to_service(self) -> None:
        """Subscribe to TiledService connection changes."""
        try:
            from lightfall.services.tiled_service import TiledService

            service = TiledService.get_instance()
            service.connection_changed.connect(self._on_connection_changed)

            # Set initial state based on current service state
            self._update_from_service(service)

        except Exception as e:
            logger.debug("Could not subscribe to TiledService: {}", e)
            self._update_display_disabled()

    def _update_from_service(self, service) -> None:
        """Update display from service state.

        Args:
            service: TiledService instance.
        """
        from lightfall.services.tiled_service import TiledConnectionState

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

    @Slot(object, str)
    def _on_connection_changed(self, state, message: str) -> None:
        """Handle connection state change.

        Args:
            state: New TiledConnectionState.
            message: Status message.
        """
        from lightfall.services.tiled_service import TiledConnectionState

        if state == TiledConnectionState.CONNECTED:
            self._update_display_connected()
        elif state == TiledConnectionState.CONNECTING:
            self._update_display_connecting()
        elif state == TiledConnectionState.ERROR:
            self._update_display_error(message)
        elif state == TiledConnectionState.DISCONNECTED:
            # Check if disabled or just disconnected
            try:
                from lightfall.services.tiled_service import TiledService

                service = TiledService.get_instance()
                if not service.config.enabled:
                    self._update_display_disabled()
                else:
                    self._update_display_disconnected()
            except Exception:
                self._update_display_disconnected()

    def _update_display_connected(self) -> None:
        """Update display for connected state."""
        self.setText("Tiled: Connected")
        self.setStyleSheet(f"color: {self.COLOR_CONNECTED};")
        self.setToolTip("Connected to Tiled server")

    def _update_display_connecting(self) -> None:
        """Update display for connecting state."""
        self.setText("Tiled: Connecting...")
        self.setStyleSheet(f"color: {self.COLOR_CONNECTING};")
        self.setToolTip("Connecting to Tiled server...")

    def _update_display_error(self, message: str = "") -> None:
        """Update display for error state.

        Args:
            message: Error message for tooltip.
        """
        self.setText("Tiled: Error")
        self.setStyleSheet(f"color: {self.COLOR_ERROR};")
        tooltip = "Tiled connection error"
        if message:
            tooltip = f"Tiled error: {message}"
        self.setToolTip(tooltip)

    def _update_display_disconnected(self) -> None:
        """Update display for disconnected state."""
        self.setText("Tiled: Disconnected")
        self.setStyleSheet(f"color: {self.COLOR_DISABLED};")
        self.setToolTip("Disconnected from Tiled server")

    def _update_display_disabled(self) -> None:
        """Update display for disabled state."""
        self.setText("Tiled: Off")
        self.setStyleSheet(f"color: {self.COLOR_DISABLED};")
        self.setToolTip("Tiled integration is disabled")
