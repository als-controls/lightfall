"""Shussebora status plugin for the NCS status bar.

Shows aggregate health of the shussebora data-movement daemons via a
snail icon colored by heartbeat freshness. Clicking opens the Data
Movement panel.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Qt

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.services.shussebora_monitor import ShusseboraMonitor
from lightfall.utils.logging import logger

_STATE_TEXT = {"ok": "", "stale": "Stale", "dead": "Down", "unknown": ""}
_FALLBACK_COLORS = {"ok": "#2ecc71", "stale": "#f39c12",
                    "dead": "#e74c3c", "unknown": "#7f8c8d"}


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
        self._theme_manager = None

    @property
    def name(self) -> str:
        return "shussebora_status"

    def _get_monitor(self) -> ShusseboraMonitor:
        if self._monitor is None:
            self._monitor = ShusseboraMonitor.get_instance()
        return self._monitor

    def create_widget(self, parent=None):
        button = super().create_widget(parent)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        return button

    # -- wiring -------------------------------------------------------------

    def connect_signals(self) -> None:
        try:
            from lightfall.ui.theme import ThemeManager

            self._theme_manager = ThemeManager.get_instance()
            self._theme_manager.colors_changed.connect(self.update)
        except Exception as e:
            logger.debug("Could not connect ThemeManager: {}", e)

        monitor = self._get_monitor()
        monitor.start()
        monitor.sigStatus.connect(self._on_monitor_changed)
        monitor.sigStateChanged.connect(self._on_monitor_changed)

    def disconnect_signals(self) -> None:
        monitor = self._get_monitor()
        try:
            monitor.sigStatus.disconnect(self._on_monitor_changed)
            monitor.sigStateChanged.disconnect(self._on_monitor_changed)
        except RuntimeError:
            pass
        if self._theme_manager is not None:
            try:
                self._theme_manager.colors_changed.disconnect(self.update)
            except RuntimeError:
                pass

    def _on_monitor_changed(self, *args) -> None:
        self.update()

    # -- display --------------------------------------------------------------

    def _state_color(self, state: str) -> str:
        try:
            from lightfall.ui.theme import ThemeManager

            colors = ThemeManager.get_instance().colors
            return {"ok": colors.success, "stale": colors.warning,
                    "dead": colors.error, "unknown": colors.text_secondary}[state]
        except Exception:
            return _FALLBACK_COLORS[state]

    def update(self) -> None:
        monitor = self._get_monitor()
        state = monitor.worst_state()
        color = self._state_color(state)

        if self._button is not None:
            try:
                import qtawesome as qta

                self._button.setIcon(qta.icon("mdi6.snail", color=color))
            except Exception as e:
                logger.debug("Could not set snail icon: {}", e)

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

        text = _STATE_TEXT.get(state, state)
        if queue_depth and state == "ok":
            text = f"{text} · {queue_depth} queued"
        self.set_text(text)
        self.set_color(color)
        self.set_tooltip("Shussebora data movement\n" + "\n".join(tooltip_lines)
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
        data["instances"] = {
            host: inst["state"] for host, inst in monitor.instances().items()}
        return data
