"""Panel plugin that provides the Agents & Skills editor panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class AgentEditorPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "agent_editor"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel

        return AgentEditorPanel
