"""ALS beam status plugin for NCS status bar.

Displays real-time ALS synchrotron beam current, lifetime, and availability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QWidget

from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lucid.ui.toast import ToastManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.services.als_beam_status import ALSBeamData, ALSBeamStatusService


class ALSBeamStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing ALS beam status.

    Displays beam current, lifetime, and availability with color coding:
    - Green: Beam available (shutters open)
    - Red: Beam unavailable (shutters closed)
    - Gray: Offline/disconnected from API

    Example display:
        "500.3 mA | 6.6h | Available" (green)
        "500.3 mA | 6.6h | Closed" (red)
        "Offline" (gray)
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lucid.statusbar.als_beam",
        name="ALS Beam Status",
        description="Shows ALS synchrotron beam current and status",
        priority=45,  # After connection (30), before tiled (40)
        position="permanent",
        tooltip="ALS beam status - click for details",
    )

    # State colors
    COLOR_AVAILABLE = "#4CAF50"  # Green - beam available
    COLOR_CLOSED = "#F44336"  # Red - shutters closed
    COLOR_OFFLINE = "#9E9E9E"  # Gray - API unreachable

    BEAM_STATUS_URL = "https://als.lbl.gov/beam-status/"

    def __init__(self) -> None:
        """Initialize the ALS beam status plugin."""
        super().__init__()
        self._label: QLabel | None = None
        self._service: ALSBeamStatusService | None = None
        self._last_beam_available: bool | None = None  # Track for change detection

    @property
    def name(self) -> str:
        """Plugin name."""
        return "als_beam_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the ALS beam status label.

        Clicking the label opens the ALS beam status page.

        Args:
            parent: Parent widget.

        Returns:
            QLabel showing beam status.
        """
        self._label = QLabel(parent)
        self._label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._label.mousePressEvent = lambda _: self._open_beam_status_page()
        return self._label

    def _open_beam_status_page(self) -> None:
        """Open the ALS beam status page in the default browser."""
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl(self.BEAM_STATUS_URL))

    def update(self) -> None:
        """Update the label with current beam status."""
        if self._label is None:
            return

        try:
            from lucid.services.als_beam_status import ALSBeamStatusService

            service = ALSBeamStatusService.get_instance()
            self._service = service

            # Start polling if not already
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
        try:
            from lucid.services.als_beam_status import ALSBeamStatusService

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
                # Already disconnected
                pass

    @Slot(object)
    def _on_status_changed(self, data: ALSBeamData) -> None:
        """Handle beam status update.

        Args:
            data: New beam status data.
        """
        self._update_display_data(data)

    @Slot(bool)
    def _on_connection_changed(self, connected: bool) -> None:
        """Handle connection state change.

        Args:
            connected: Whether connected to API.
        """
        if not connected:
            self._update_display_offline()
        else:
            self.update()

    def _update_display_data(self, data: ALSBeamData) -> None:
        """Update display with beam data.

        Emits a toast notification when beam availability changes.

        Args:
            data: Current beam data.
        """
        if self._label is None:
            return

        # Detect beam availability change and notify
        if (
            self._last_beam_available is not None
            and data.beam_available != self._last_beam_available
        ):
            self._notify_status_change(data.beam_available)
        self._last_beam_available = data.beam_available

        # Format: "500.3 mA | 6.6h | Available"
        current_str = f"{data.beam_current:.1f} mA"
        lifetime_str = f"{data.lifetime:.1f}h"
        status_str = "Available" if data.beam_available else "Closed"

        text = f"{current_str} | {lifetime_str} | {status_str}"
        color = self.COLOR_AVAILABLE if data.beam_available else self.COLOR_CLOSED

        self._label.setText(text)
        self._label.setStyleSheet(f"color: {color};")
        self._label.setToolTip(self._build_tooltip(data))

    def _notify_status_change(self, beam_available: bool) -> None:
        """Show a toast notification when ring status changes.

        Args:
            beam_available: Whether beam is now available.
        """
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
        if self._label is None:
            return

        self._label.setText("Offline")
        self._label.setStyleSheet(f"color: {self.COLOR_OFFLINE};")

        error_msg = ""
        if self._service and self._service.last_error:
            error_msg = f"\nError: {self._service.last_error}"

        self._label.setToolTip(f"ALS beam status unavailable{error_msg}")

    def _build_tooltip(self, data: ALSBeamData) -> str:
        """Build detailed tooltip from beam data.

        Args:
            data: Current beam data.

        Returns:
            Formatted tooltip string.
        """
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
            from lucid.services.als_beam_status import ALSBeamStatusService

            service = ALSBeamStatusService.get_instance()
            data.update(service.get_introspection_data())

        except Exception:
            data["als_beam_connected"] = False

        return data
