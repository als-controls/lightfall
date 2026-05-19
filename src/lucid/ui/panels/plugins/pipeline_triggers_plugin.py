"""Pipeline Triggers panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucid.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel


class PipelineTriggersPanelPlugin(PanelPlugin):
    """Panel plugin that surfaces the Pipeline Triggers dock panel."""

    @property
    def name(self) -> str:
        return "pipeline_triggers"

    def get_panel_class(self) -> type[BasePanel]:
        from lucid.ui.panels.pipeline_triggers_panel import (
            PipelineTriggersDockPanel,
        )

        return PipelineTriggersDockPanel
