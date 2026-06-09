"""ALS beam status plugin for NCS status bar.

Displays real-time ALS synchrotron beam current, lifetime, and availability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import qtawesome as qta
from PySide6.QtCore import QUrl, Slot
from PySide6.QtGui import QDesktopServices

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.ui.theme import ThemeManager
from lightfall.ui.toast import ToastManager
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.services.als_beam_status import ALSBeamData, ALSBeamStatusService


class ALSBeamStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing ALS beam status.

    Displays beam current, lifetime, and availability with theme-aware coloring:
    - success: Beam available (shutters open)
    - error: Beam unavailable (shutters closed)
    - text_secondary: Offline/disconnected from API

    Clicking opens the ALS beam status page.
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.als_beam",
        name="ALS Beam Status",
        description="Shows ALS synchrotron beam current and status",
        priority=45,
        position="permanent",
        tooltip="ALS beam status - click for details",
    )

    BEAM_STATUS_URL = "https://als.lbl.gov/beam-status/"

    def __init__(self) -> None:
        """Initialize the ALS beam status plugin."""
        super().__init__()
        self._service: ALSBeamStatusService | None = None
        self._last_beam_available: bool | None = None
        self._theme_manager: ThemeManager | None = None

    @property
    def name(self) -> str:
        """Plugin name."""
        return "als_beam_status"

    def on_clicked(self) -> None:
        """Open the ALS beam status page in the default browser."""
        QDesktopServices.openUrl(QUrl(self.BEAM_STATUS_URL))

    def update(self) -> None:
        """Update the button with current beam status."""
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()

        try:
            from lightfall.services.als_beam_status import ALSBeamStatusService

            service = ALSBeamStatusService.get_instance()
            self._service = service

            if not service.is_polling:
                service.start_polling()

            if not service.is_connected:
                self._update_display_offline()
            elif service.current_data is not None:
                self._update_display_data(service.current_data)
            else:
                self._update_display_offline()

        except Exception as e:
            logger.debug("Could not get ALS beam status: {}", e)
            self._update_display_offline()

    def connect_signals(self) -> None:
        """Connect to beam status service signals."""
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        self._theme_manager.colors_changed.connect(self.update)

        try:
            from lightfall.services.als_beam_status import ALSBeamStatusService

            service = ALSBeamStatusService.get_instance()
            self._service = service
            service.status_changed.connect(self._on_status_changed)
            service.connection_changed.connect(self._on_connection_changed)

        except Exception as e:
            logger.debug("Could not connect to ALSBeamStatusService: {}", e)

    def disconnect_signals(self) -> None:
        """Disconnect from service signals."""
        if self._service is not None:
            try:
                self._service.status_changed.disconnect(self._on_status_changed)
                self._service.connection_changed.disconnect(self._on_connection_changed)
            except RuntimeError:
                pass

        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    @Slot(object)
    def _on_status_changed(self, data: ALSBeamData) -> None:
        """Handle beam status update."""
        self._update_display_data(data)

    @Slot(bool)
    def _on_connection_changed(self, connected: bool) -> None:
        """Handle connection state change."""
        if not connected:
            self._update_display_offline()
        else:
            self.update()

    def _update_display_data(self, data: ALSBeamData) -> None:
        """Update display with beam data; toast on availability change."""
        if (
            self._last_beam_available is not None
            and data.beam_available != self._last_beam_available
        ):
            self._notify_status_change(data.beam_available)
        self._last_beam_available = data.beam_available

        current_str = f"{data.beam_current:.1f} mA"
        lifetime_str = f"{data.lifetime:.1f}h"

        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()
        colors = self._theme_manager.colors
        color = colors.success if data.beam_available else colors.error

        self.set_icon(qta.icon("ri.camera-lens-line", color=color))
        if data.beam_current < 450 or data.lifetime < 1 or not data.beam_available:
            self.set_text(f"{current_str} | {lifetime_str}")
        else:
            self.set_text("")
        self.set_color(color)
        self.set_tooltip(self._build_tooltip(data))

    def _notify_status_change(self, beam_available: bool) -> None:
        """Show a toast notification when ring status changes."""
        toast = ToastManager.get_instance()
        link = f'<a href="{self.BEAM_STATUS_URL}">Beam Status</a>'

        if beam_available:
            toast.success(
                "ALS Ring Open",
                f"Beam is now available · {link}",
                duration=10000,
            )
        else:
            toast.warning(
                "ALS Ring Closed",
                f"Beam is no longer available · {link}",
                duration=10000,
            )

    def _update_display_offline(self) -> None:
        """Update display for offline/error state."""
        if self._theme_manager is None:
            self._theme_manager = ThemeManager.get_instance()

        secondary = self._theme_manager.colors.text_secondary
        self.set_icon(qta.icon("ri.camera-lens-line", color=secondary))
        self.set_text("Offline")
        self.set_color(secondary)

        error_msg = ""
        if self._service and self._service.last_error:
            error_msg = f"\nError: {self._service.last_error}"
        self.set_tooltip(f"ALS beam status unavailable{error_msg}")

    def _build_tooltip(self, data: ALSBeamData) -> str:
        """Build detailed tooltip from beam data."""
        lines = [
            "ALS Beam Status",
            "─" * 25,
            f"Current: {data.beam_current:.1f} mA",
            f"Energy: {data.beam_energy:.2f} GeV",
            f"Lifetime: {data.lifetime:.1f} hours",
            f"Status: {'Available' if data.beam_available else 'Shutters Closed'}",
            "",
            f"X RMS: {data.x_rms:.1f} μm",
            f"Y RMS: {data.y_rms:.1f} μm",
        ]

        if data.comment:
            lines.extend(["", "Operations:", data.comment])

        if data.timestamp:
            lines.extend(["", f"Updated: {data.timestamp.strftime('%H:%M:%S')}"])

        return "\n".join(lines)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        try:
            from lightfall.services.als_beam_status import ALSBeamStatusService

            service = ALSBeamStatusService.get_instance()
            data.update(service.get_introspection_data())

        except Exception:
            data["als_beam_connected"] = False

        return data
