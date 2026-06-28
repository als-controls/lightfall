"""Panel plugin that provides the Monitor panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class MonitorPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "monitor"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.monitor_panel import MonitorPanel
        return MonitorPanel
