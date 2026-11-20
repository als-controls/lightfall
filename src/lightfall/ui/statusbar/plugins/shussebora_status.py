"""Shussebora status plugin for the NCS status bar.

Shows aggregate health of the shussebora data-movement daemons via a
snail icon colored by heartbeat freshness. Clicking opens the Data
Movement panel.
"""

from __future__ import annotations

from typing import Any, ClassVar

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.services.shussebora_monitor import ShusseboraMonitor
from lightfall.utils.logging import logger

_STATE_TEXT = {"ok": "OK", "stale": "Stale", "dead": "Down", "unknown": ""}
_STATE_ROLE = {"ok": "success", "stale": "warning", "dead": "error",
               "unknown": "text_secondary"}


class ShusseboraStatusPlugin(StatusBarPlugin):
    """Status bar indicator for shussebora data-movement daemons.

    Color coding (worst state across instances):
    - success: heartbeats fresh on every instance
    - warning: a heartbeat has gone stale (>150 s)
    - error: no heartbeat for >300 s — service likely down
    - text_secondary: no instance seen yet
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.shussebora",
        name="Data Movement Status",
        description="Shows shussebora data-movement daemon health",
        priority=45,
        position="permanent",
        tooltip="Shussebora data movement status",
    )

    def __init__(self, monitor: ShusseboraMonitor | None = None) -> None:
        super().__init__()
        self._monitor = monitor

    @property
    def name(self) -> str:
        return "shussebora_status"

    def _get_monitor(self) -> ShusseboraMonitor:
        if self._monitor is None:
            self._monitor = ShusseboraMonitor.get_instance()
        return self._monitor

    # -- wiring -------------------------------------------------------------

    def connect_signals(self) -> None:
        monitor = self._get_monitor()
        monitor.start()
        monitor.sigStatus.connect(self._on_monitor_changed)
        monitor.sigStateChanged.connect(self._on_monitor_changed)
        monitor.sigDiskPressure.connect(self._on_monitor_changed)

    def disconnect_signals(self) -> None:
        monitor = self._get_monitor()
        try:
            monitor.sigStatus.disconnect(self._on_monitor_changed)
            monitor.sigStateChanged.disconnect(self._on_monitor_changed)
            monitor.sigDiskPressure.disconnect(self._on_monitor_changed)
        except RuntimeError:
            pass
    def _on_monitor_changed(self, *args) -> None:
        self.update()

    # -- display --------------------------------------------------------------

    def update(self) -> None:
        monitor = self._get_monitor()
        state = monitor.worst_state()
        pressure = monitor.disk_pressure()
        if pressure and state in ("ok", "unknown"):
            state_for_color = "stale"  # warning color
        else:
            state_for_color = state
        role = _STATE_ROLE[state_for_color]

        instances = monitor.instances()
        queue_depth = 0
        tooltip_lines = []
        for hostname, inst in sorted(instances.items()):
            status = inst.get("status", {})
            counts = status.get("transfers", {})
            host_queue = sum(t.get("queue_depth", 0)
                             for t in status.get("triggers", []))
            queue_depth += host_queue
            tooltip_lines.append(
                f"{hostname}: {_STATE_TEXT.get(inst['state'], inst['state'])}, "
                f"queue {host_queue}, 24h {counts.get('complete_24h', 0)} ok / "
                f"{counts.get('failed_24h', 0)} failed")

        for hostname, info in sorted(pressure.items()):
            pct = info.get("percent_used", info.get("used_pct"))
            pct_text = f" ({pct:g}% used)" if isinstance(pct, (int, float)) else ""
            tooltip_lines.append(f"Disk pressure on {hostname}{pct_text}")

        text = _STATE_TEXT.get(state, state)
        if state == "ok":
            # Healthy: the snail icon already conveys health, so the button
            # shows only the queue depth (or nothing when idle) rather than
            # an "OK" label. The "OK" label still appears in the tooltip.
            text = f"{queue_depth} queued" if queue_depth else ""
        if pressure and state == "ok" and not text:
            text = "Disk"
        self.set_state(text, icon="mdi6.snail", role=role,
                       tooltip="Shussebora data movement\n" + "\n".join(tooltip_lines)
                       if tooltip_lines else "Shussebora: no instances seen on the bus")

    # -- interaction -------------------------------------------------------------

    def on_clicked(self) -> None:
        """Open the Data Movement panel."""
        try:
            from lightfall.core.application import LFApplication

            app = LFApplication.get_instance()
            if app and app.main_window:
                app.main_window.add_panel("lightfall.panels.shussebora")
        except Exception as e:
            logger.debug("Could not open Data Movement panel: {}", e)

    def get_introspection_data(self) -> dict[str, Any]:
        data = super().get_introspection_data()
        monitor = self._get_monitor()
        data["worst_state"] = monitor.worst_state()
        data["disk_pressure"] = sorted(monitor.disk_pressure())
        data["instances"] = {
            host: inst["state"] for host, inst in monitor.instances().items()}
        return data
