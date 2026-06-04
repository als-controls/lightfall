"""Threads panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class ThreadsPanelPlugin(PanelPlugin):
    """Panel plugin that provides the Threads panel.

    The Threads panel displays background threads managed by ThreadManager
    and allows monitoring and management of thread execution.
    """

    @property
    def name(self) -> str:
        return "threads"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.threads_panel import ThreadsPanel

        return ThreadsPanel
