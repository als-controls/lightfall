"""Princeton PIMTE camera control widget.

Extends CameraControlWidget with PIMTE-specific temperature display.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtWidgets import QGroupBox, QWidget

from lucid.ui.widgets.base_control import register_control_widget
from lucid.ui.widgets.camera.base import CameraControlWidget
from lucid.ui.widgets.camera.panels.temperature import TemperaturePanel


@register_control_widget
class PIMTECameraControlWidget(CameraControlWidget):
    """Control widget for Princeton PIMTE cameras.

    Extends the base camera control with:
    - Sensor temperature display
    - Temperature setpoint control
    - Actual setpoint readback

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
        # Create temperature panel with current prefix
        self._temp_panel = TemperaturePanel(
            prefix=self._prefix,
            cam_suffix=self._cam_suffix,
        )
        return [self._temp_panel]

    def _extract_prefix(self, item) -> None:
        """Extract prefix and update temperature panel."""
        super()._extract_prefix(item)

        # Update temperature panel prefix if it exists
        if self._temp_panel is not None:
            self._temp_panel.set_prefix(self._prefix)

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
