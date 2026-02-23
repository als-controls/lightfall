"""Logbook entries sidebar panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucid.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel


class LogbookEntriesPanelPlugin(PanelPlugin):
    """Panel plugin that provides the Logbook Entries sidebar panel."""

    @property
    def name(self) -> str:
        return "logbook_entries"

    def get_panel_class(self) -> type[BasePanel]:
        from lucid.ui.panels.logbook_entries_panel import LogbookEntriesPanel

        return LogbookEntriesPanel
