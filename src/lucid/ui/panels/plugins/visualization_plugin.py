"""Visualization panel plugin.

Registers the VisualizationPanel with the plugin system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucid.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel


class VisualizationPanelPlugin(PanelPlugin):
    """Plugin providing the visualization panel."""

    @property
    def name(self) -> str:
        return "visualization"

    def get_panel_class(self) -> type[BasePanel]:
        """Get the panel class."""
        from lucid.ui.panels.visualization_panel import VisualizationPanel

        return VisualizationPanel
