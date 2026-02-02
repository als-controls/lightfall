"""Andor camera control widget.

Extends PlanBasedCameraControlWidget with Andor-specific cooler controls.
Uses ophyd's uniform signal interface for all device communication.
Supports plan-based acquisition with dark frame collection.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtWidgets import QGroupBox, QWidget

from lucid.ui.models.device_tree import DeviceTreeItem
from lucid.ui.widgets.base_control import register_control_widget
from lucid.ui.widgets.camera.plan_based import PlanBasedCameraControlWidget
from lucid.ui.widgets.camera.panels.cooler import CoolerPanel


@register_control_widget
class AndorCameraControlWidget(PlanBasedCameraControlWidget):
    """Control widget for Andor cameras with cooler support.

    Extends the plan-based camera control with:
    - Cooler on/off control
    - Temperature setpoint
    - Temperature readback
    - Cooler status display
    - Dark frame collection support

    Uses ophyd's uniform signal interface for all device communication.
    Matches devices with "andor" tag or "Andor" in device_class.
    """

    display_name: ClassVar[str] = "Andor Camera"
    priority: ClassVar[int] = 100  # Device-specific match

    # Match Andor cameras by tag or class name
    supported_tags: ClassVar[set[str]] = {"andor"}
    supported_classes: ClassVar[set[str]] = {"Andor", "AndorCamera", "AndorDetector"}

    def __init__(self, parent: QWidget | None = None) -> None:
        self._cooler_panel: CoolerPanel | None = None
        super().__init__(parent)

    def _create_device_panels(self) -> list[QGroupBox]:
        """Create the Andor cooler panel."""
        # Create cooler panel - device will be set when set_items is called
        self._cooler_panel = CoolerPanel()
        return [self._cooler_panel]

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the camera device to control."""
        super().set_items(items)

        # Update cooler panel with the device
        if self._cooler_panel is not None:
            self._cooler_panel.set_device(self._device)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        # Add cooler data
        if self._cooler_panel is not None:
            data["cooler"] = self._cooler_panel.get_introspection_data()

        return data

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        if self._cooler_panel is not None:
            self._cooler_panel.close()
        super().closeEvent(event)
