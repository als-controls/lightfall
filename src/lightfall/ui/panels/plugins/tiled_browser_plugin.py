"""Tiled browser panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class TiledBrowserPanelPlugin(PanelPlugin):
    """Panel plugin that provides the Tiled data browser panel.

    The Tiled browser panel allows users to browse and search data
    stored in a Tiled server, with filtering and record selection.
    """

    @property
    def name(self) -> str:
        return "tiled_browser"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.tiled_browser_panel import TiledBrowserPanel

        return TiledBrowserPanel
