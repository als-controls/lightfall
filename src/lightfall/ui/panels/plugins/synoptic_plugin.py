"""Synoptic panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class SynopticPanelPlugin(PanelPlugin):
    """Panel plugin that provides the 3D Synoptic panel.

    The Synoptic panel displays a 3D visualization of beamline hardware,
    allowing users to visualize device layouts and (with permission)
    edit device positions.

    Note: Requires PyQtGraph with OpenGL support.
    """

    @property
    def name(self) -> str:
        return "synoptic"

    def get_panel_class(self) -> type[BasePanel]:
        try:
            from lightfall.ui.panels.synoptic.panel import SynopticPanel

            return SynopticPanel
        except ImportError as e:
            logger.error(
                "Failed to import SynopticPanel (pyqtgraph.opengl may be unavailable): {}",
                e,
            )
            raise
