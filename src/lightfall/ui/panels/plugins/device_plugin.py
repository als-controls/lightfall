"""Device panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class DevicePanelPlugin(PanelPlugin):
    """Panel plugin that provides the Device panel.

    The Device panel displays connected hardware devices and allows
    monitoring and controlling their values.
    """

    @property
    def name(self) -> str:
        return "devices"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.device_panel import DevicePanel

        return DevicePanel
