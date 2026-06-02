"""IPython panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucid.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel


class IPythonPanelPlugin(PanelPlugin):
    """Panel plugin that provides the IPython console panel.

    The IPython panel provides an embedded interactive Python console
    with access to live application objects and widget targeting.
    """

    @property
    def name(self) -> str:
        return "ipython"

    def get_panel_class(self) -> type[BasePanel]:
        from lucid.ui.panels.ipython_panel import IPythonPanel

        return IPythonPanel
