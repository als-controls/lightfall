"""NATS connection status plugin for the Lightfall status bar.

Shows whether Lightfall is attached to the NATS / IPC bus, using variants of
the ``mdi6.message`` icon. Hovering lists the active peers discovered on the
bus; clicking refreshes that list. Peers are gathered via a scatter-gather
over the well-known ``_lightfall.discover`` subject.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import qtawesome as qta

from lightfall.ipc.service import IPCService, get_ipc_service
from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.utils.logging import logger

_FALLBACK_COLORS = {
    "connected": "#2ecc71",
    "disconnected": "#e74c3c",
    "off": "#7f8c8d",
}


class NatsStatusPlugin(StatusBarPlugin):
    """Status bar indicator for the NATS / IPC bus connection.

    States:
    - connected: ``mdi6.message-text``, success color, peer count (excl. self)
    - disconnected (configured, down): ``mdi6.message-off-outline``, error color
    - not configured (no IPCService): ``mdi6.message-outline``, secondary color
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.nats",
        name="NATS Status",
        description="Shows NATS IPC connection status and active peers",
        priority=35,
        position="permanent",
        tooltip="NATS connection status",
    )

    def __init__(self, ipc: IPCService | None = None) -> None:
        super().__init__()
        self._ipc = ipc
        self._theme_manager = None
        self._peers: list[dict] = []
        self._last_refreshed: str | None = None
        self._icon_name: str = "mdi6.message-outline"

    @property
    def name(self) -> str:
        return "nats_status"

    def _get_ipc(self) -> IPCService | None:
        if self._ipc is None:
            self._ipc = get_ipc_service()
        return self._ipc

    # -- wiring ---------------------------------------------------------------

    def connect_signals(self) -> None:
        try:
            from lightfall.ui.theme import ThemeManager

            self._theme_manager = ThemeManager.get_instance()
            self._theme_manager.colors_changed.connect(self.update)
        except Exception as e:
            logger.debug("NatsStatus: could not connect ThemeManager: {}", e)

        ipc = self._get_ipc()
        if ipc is not None:
            ipc.sigConnectionChanged.connect(self._on_connection_changed)
            if ipc.is_connected:
                self._refresh_peers()

    def disconnect_signals(self) -> None:
        ipc = self._get_ipc()
        if ipc is not None:
            try:
                ipc.sigConnectionChanged.disconnect(self._on_connection_changed)
            except RuntimeError:
                pass
        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    def _on_connection_changed(self, connected: bool) -> None:
        if connected:
            self._refresh_peers()
        else:
            self._peers = []
            self.update()

    # -- peer refresh ---------------------------------------------------------

    def _refresh_peers(self) -> None:
        ipc = self._get_ipc()
        if ipc is None or not ipc.is_connected:
            self.update()
            return
        ipc.discover_peers(self._on_peers)

    def _on_peers(self, peers: list[dict]) -> None:
        self._peers = peers
        self._last_refreshed = datetime.now().strftime("%H:%M:%S")
        self.update()

    # -- display --------------------------------------------------------------

    def _color(self, key: str) -> str:
        try:
            from lightfall.ui.theme import ThemeManager

            colors = ThemeManager.get_instance().colors
            return {
                "connected": colors.success,
                "disconnected": colors.error,
                "off": colors.text_secondary,
            }[key]
        except Exception:
            return _FALLBACK_COLORS[key]

    def update(self) -> None:
        ipc = self._get_ipc()

        if ipc is None:
            self._icon_name = "mdi6.message-outline"
            self._apply("off", "Off", "NATS IPC not configured")
            return

        if not ipc.is_connected:
            self._icon_name = "mdi6.message-off-outline"
            self._apply("disconnected", "", "NATS: not connected")
            return

        self._icon_name = "mdi6.message-text"
        others = [p for p in self._peers if not p.get("is_self")]
        text = str(len(others)) if others else ""
        self._apply("connected", text, self._build_tooltip(ipc))

    def _apply(self, color_key: str, text: str, tooltip: str) -> None:
        color = self._color(color_key)
        try:
            self.set_icon(qta.icon(self._icon_name, color=color))
        except Exception as e:
            logger.debug("NatsStatus: could not set icon {}: {}", self._icon_name, e)
        self.set_text(text)
        self.set_color(color)
        self.set_tooltip(tooltip)

    def _build_tooltip(self, ipc: IPCService) -> str:
        lines = ["NATS connection — Connected", f"Server: {ipc.nats_url}", ""]
        if self._peers:
            lines.append(f"Peers ({len(self._peers)}):")
            for p in self._peers:
                name = p.get("display_name") or p.get("instance_id") or "?"
                tag = " (this instance)" if p.get("is_self") else ""
                lines.append(f"  • {name}{tag} — {p.get('instance_id', '')}")
        else:
            lines.append("Peers: click to refresh")
        lines.append("")
        if self._last_refreshed:
            lines.append(f"Last refreshed: {self._last_refreshed}  ·  click to refresh")
        else:
            lines.append("Click to refresh peers")
        return "\n".join(lines)

    # -- interaction ----------------------------------------------------------

    def on_clicked(self) -> None:
        self._refresh_peers()

    def get_introspection_data(self) -> dict[str, Any]:
        data = super().get_introspection_data()
        ipc = self._get_ipc()
        data["connected"] = bool(ipc is not None and ipc.is_connected)
        data["peers"] = list(self._peers)
        data["peer_count"] = len([p for p in self._peers if not p.get("is_self")])
        return data
