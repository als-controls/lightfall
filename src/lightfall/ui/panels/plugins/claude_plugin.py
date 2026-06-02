"""Claude panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class ClaudePanelPlugin(PanelPlugin):
    """Panel plugin that provides the Claude AI assistant panel.

    The Claude panel provides an AI assistant interface for interacting
    with the control system using natural language.

    Note: This plugin is built into LUCID.
    """

    @property
    def name(self) -> str:
        return "claude"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.claude_panel import ClaudePanel

        return ClaudePanel
