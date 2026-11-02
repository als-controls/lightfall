"""Panel plugin that provides the Shared content panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class ContentPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "shared_content"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.content_panel import ContentPanel

        return ContentPanel
