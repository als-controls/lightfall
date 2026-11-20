"""Caproxy lease status plugin for NCS status bar.

Displays the state of caproxy attested leases: idle, pending, active
(with a live mm:ss countdown to the soonest expiry), or a poll error.
See docs/plans/2026-07-23-caproxy-lease-ux.md.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from PySide6.QtCore import QTimer, Slot

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.utils.logging import logger

# Countdown re-render cadence between service polls.
_TICK_INTERVAL_MS = 1000


def _is_active(record: dict[str, Any]) -> bool:
    return str(record.get("state", "")).lower() == "active"


def _is_pending(record: dict[str, Any]) -> bool:
    return str(record.get("state", "")).lower() == "pending"


def _format_countdown(seconds_remaining: float) -> str:
    """Format a non-negative countdown as ``mm:ss`` (floored, clamped at 0)."""
    total = max(0, int(seconds_remaining))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


class CaproxyLeaseStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing caproxy attested-lease state.

    Displays state with theme-aware color coding:
    - text_secondary: No active or pending leases.
    - warning: A lease request is pending approval.
    - success: An active lease is in effect (countdown to soonest expiry).
    - error: The lease-polling loop cannot reach the caproxy server.
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.caproxy_lease",
        name="Caproxy Lease Status",
        description="Shows caproxy attested-lease state (pending/active/error)",
        priority=45,
        position="permanent",
        tooltip="Caproxy lease status",
    )

    def __init__(self) -> None:
        """Initialize the caproxy lease status plugin."""
        super().__init__()
        self._service = None
        self._leases: list[dict[str, Any]] = []
        self._poll_error: str | None = None
        self._timer = QTimer()
        self._timer.setInterval(_TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

    @property
    def name(self) -> str:
        """Plugin name."""
        return "caproxy_lease_status"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect_signals(self) -> None:
        """Connect to the CaproxyLeaseService singleton and start polling."""

        try:
            from lightfall.services.caproxy_lease_service import CaproxyLeaseService

            service = CaproxyLeaseService.get_instance()
            self._service = service
            service.leases_updated.connect(self._on_leases_updated)
            service.poll_error.connect(self._on_poll_error)
            service.start_polling()
        except Exception as e:
            logger.debug("Could not connect to CaproxyLeaseService: {}", e)

        self._timer.start()

    def disconnect_signals(self) -> None:
        """Disconnect from the service and stop the countdown timer."""
        self._timer.stop()

        if self._service is not None:
            try:
                self._service.leases_updated.disconnect(self._on_leases_updated)
            except RuntimeError:
                pass
            try:
                self._service.poll_error.disconnect(self._on_poll_error)
            except RuntimeError:
                pass
            try:
                self._service.stop_polling()
            except Exception as e:
                logger.debug("Could not stop caproxy lease polling: {}", e)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    @Slot(list)
    def _on_leases_updated(self, leases: list) -> None:
        self._poll_error = None
        self._leases = list(leases)
        self.update()

    @Slot(str)
    def _on_poll_error(self, error_text: str) -> None:
        self._poll_error = error_text
        self.update()

    def _on_tick(self) -> None:
        """Re-render the countdown between polls without waiting on new data."""
        if self._poll_error is None:
            active = [r for r in self._leases if _is_active(r)]
            if active:
                self.update()

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------



    def update(self) -> None:
        """Recompute the display from the last known lease snapshot."""
        if self._poll_error is not None:
            self._update_display_error(self._poll_error)
            return

        active = [r for r in self._leases if _is_active(r)]
        pending = [r for r in self._leases if _is_pending(r)]

        if active:
            self._update_display_active(active)
        elif pending:
            self._update_display_pending(pending)
        else:
            self._update_display_idle()

    def _update_display_idle(self) -> None:
        self.set_state("", icon="mdi6.lock-outline", role="text_secondary", tooltip="No active leases")

    def _update_display_pending(self, pending: list[dict[str, Any]]) -> None:
        patterns = [", ".join(r.get("pv_patterns", []) or []) for r in pending]
        tooltip = "Lease pending approval:\n" + "\n".join(patterns) if patterns else (
            "Lease pending approval"
        )
        self.set_state("lease pending", icon="mdi6.lock-clock", role="warning", tooltip=tooltip)

    def _update_display_active(self, active: list[dict[str, Any]]) -> None:
        now = time.time()
        soonest = min(active, key=lambda r: r.get("expires_at", 0))
        remaining = soonest.get("expires_at", 0) - now
        countdown = _format_countdown(remaining)

        lines = []
        for r in active:
            patterns = ", ".join(r.get("pv_patterns", []) or [])
            expires_in = _format_countdown(r.get("expires_at", 0) - now)
            lines.append(f"{patterns} (expires in {expires_in})")
        tooltip = "Active leases:\n" + "\n".join(lines) if lines else "Active lease"

        self.set_state(countdown, icon="mdi6.lock-open-variant", role="success", tooltip=tooltip)

    def _update_display_error(self, message: str) -> None:
        tooltip = f"Caproxy lease poll error: {message}" if message else (
            "Caproxy lease poll error"
        )
        self.set_state("lease ?", icon="mdi6.lock-alert", role="error", tooltip=tooltip)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def on_clicked(self) -> None:
        """Open the Request Unlock dialog."""
        try:
            from lightfall.core.application import LFApplication
            from lightfall.ui.dialogs import LeaseRequestDialog

            app = LFApplication.get_instance()
            parent = app.main_window if app else None
            dialog = LeaseRequestDialog(parent)
            dialog.exec()
        except Exception as e:
            logger.debug("Could not open Request Unlock dialog: {}", e)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()
        data["lease_count"] = len(self._leases)
        data["poll_error"] = self._poll_error
        return data
