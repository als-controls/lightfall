"""Logbook panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class LogbookPanelPlugin(PanelPlugin):
    """Panel plugin that provides the Logbook panel.

    The Logbook panel provides an electronic experiment notebook for
    recording observations, notes, and automatically logging events.
    """

    @property
    def name(self) -> str:
        return "logbook"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.logbook_panel import LogbookPanel

        return LogbookPanel
