"""Logging panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ncs.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from ncs.ui.panels.base import BasePanel


class LoggingPanelPlugin(PanelPlugin):
    """Panel plugin that provides the Logging panel.

    The Logging panel displays application logs in real-time
    with filtering by log level.
    """

    @property
    def name(self) -> str:
        return "logging"

    def get_panel_class(self) -> type[BasePanel]:
        from ncs.ui.panels.logging_panel import LoggingPanel

        return LoggingPanel
