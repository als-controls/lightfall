"""Queue panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucid.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel


class QueuePanelPlugin(PanelPlugin):
    """Panel plugin that provides the Queue management panel.

    The Queue panel provides an interface for managing the RunEngine queue:
    - View and reorder pending procedures
    - Edit procedure priority and parameters
    - View execution history with retry options
    """

    @property
    def name(self) -> str:
        return "queue"

    def get_panel_class(self) -> type[BasePanel]:
        from lucid.ui.panels.queue import QueuePanel

        return QueuePanel
