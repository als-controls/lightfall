"""Shussebora data-movement status panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class ShusseboraPanelPlugin(PanelPlugin):
    """Panel plugin that provides the Data Movement (shussebora) panel.

    Shows the health of shussebora data-movement daemons on the NATS bus:
    heartbeats, EPICS trigger connections, queue depth, disk usage, and
    recent transfer outcomes.
    """

    @property
    def name(self) -> str:
        return "shussebora"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.shussebora_panel import ShusseboraPanel

        return ShusseboraPanel
