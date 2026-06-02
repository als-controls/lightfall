"""Pipeline Jobs panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucid.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel


class PipelineJobsPanelPlugin(PanelPlugin):
    """Panel plugin that surfaces the Pipeline Jobs dock panel."""

    @property
    def name(self) -> str:
        return "pipeline_jobs"

    def get_panel_class(self) -> type[BasePanel]:
        from lucid.ui.panels.pipeline_jobs_panel import PipelineJobsDockPanel

        return PipelineJobsDockPanel
