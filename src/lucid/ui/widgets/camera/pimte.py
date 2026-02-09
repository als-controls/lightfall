"""Princeton PIMTE camera control widget.

Extends PlanBasedCameraControlWidget with PIMTE-specific temperature display.
Uses ophyd's uniform signal interface for all device communication.
Supports plan-based acquisition with dark frame collection.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtWidgets import QGroupBox, QWidget

from lucid.ui.models.device_tree import DeviceTreeItem
from lucid.ui.widgets.base_control import register_control_widget
from lucid.ui.widgets.camera.panels.temperature import TemperaturePanel
from lucid.ui.widgets.camera.plan_based import PlanBasedCameraControlWidget


@register_control_widget
class PIMTECameraControlWidget(PlanBasedCameraControlWidget):
    """Control widget for Princeton PIMTE cameras.

    Extends the plan-based camera control with:
    - Sensor temperature display
    - Temperature setpoint control
    - Actual setpoint readback
    - Dark frame collection support

    Uses ophyd's uniform signal interface for all device communication.
    Matches devices with "pimte" tag or "PIMTE" in device_class.
    """

    display_name: ClassVar[str] = "PIMTE Camera"
    priority: ClassVar[int] = 100  # Device-specific match

    # Match PIMTE cameras by tag or class name
    supported_tags: ClassVar[set[str]] = {"pimte", "mte", "princeton"}
    supported_classes: ClassVar[set[str]] = {"PIMTE", "MTE", "ProEM", "Princeton"}

    def __init__(self, parent: QWidget | None = None) -> None:
        self._temp_panel: TemperaturePanel | None = None
        super().__init__(parent)

    def _create_device_panels(self) -> list[QGroupBox]:
        """Create the PIMTE temperature panel."""
        # Create temperature panel - device will be set when set_items is called
        self._temp_panel = TemperaturePanel()
        return [self._temp_panel]

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the camera device to control."""
        super().set_items(items)

        # Update temperature panel with the device
        if self._temp_panel is not None:
            self._temp_panel.set_device(self._device)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()

        # Add temperature data
        if self._temp_panel is not None:
            data["temperature"] = self._temp_panel.get_introspection_data()

        return data

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        if self._temp_panel is not None:
            self._temp_panel.close()
        super().closeEvent(event)
