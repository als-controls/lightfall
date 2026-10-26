"""Panel plugin that provides the Checkpoints panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class CheckpointsPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "checkpoints"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.checkpoints_panel import CheckpointsPanel
        return CheckpointsPanel
