"""Bluesky panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucid.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel


class BlueskyPanelPlugin(PanelPlugin):
    """Panel plugin that provides the Bluesky panel.

    The Bluesky panel provides an interface for running data acquisition
    plans using the Bluesky RunEngine.
    """

    @property
    def name(self) -> str:
        return "bluesky"

    def get_panel_class(self) -> type[BasePanel]:
        from lucid.ui.panels.bluesky_panel import BlueskyPanel

        return BlueskyPanel
