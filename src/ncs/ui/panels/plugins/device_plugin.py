"""Device panel plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ncs.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from ncs.ui.panels.base import BasePanel


class DevicePanelPlugin(PanelPlugin):
    """Panel plugin that provides the Device panel.

    The Device panel displays connected hardware devices and allows
    monitoring and controlling their values.
    """

    @property
    def name(self) -> str:
        return "devices"

    def get_panel_class(self) -> type[BasePanel]:
        from ncs.ui.panels.device_panel import DevicePanel

        return DevicePanel
