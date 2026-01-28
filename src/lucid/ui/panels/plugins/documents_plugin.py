"""Documents panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucid.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel


class DocumentsPanelPlugin(PanelPlugin):
    """Panel plugin that provides the Documents panel.

    The Documents panel displays project documents and files.
    """

    @property
    def name(self) -> str:
        return "documents"

    def get_panel_class(self) -> type[BasePanel]:
        from lucid.ui.panels.documents_panel import DocumentsPanel

        return DocumentsPanel
