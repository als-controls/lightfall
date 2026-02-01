"""Andor camera control widget.

Extends CameraControlWidget with Andor-specific cooler controls.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtWidgets import QGroupBox, QWidget

from lucid.ui.widgets.base_control import register_control_widget
from lucid.ui.widgets.camera.base import CameraControlWidget
from lucid.ui.widgets.camera.panels.cooler import CoolerPanel


@register_control_widget
class AndorCameraControlWidget(CameraControlWidget):
    """Control widget for Andor cameras with cooler support.

    Extends the base camera control with:
    - Cooler on/off control
    - Temperature setpoint
    - Temperature readback
    - Cooler status display

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
        # Create cooler panel with current prefix
        self._cooler_panel = CoolerPanel(
            prefix=self._prefix,
            cam_suffix=self._cam_suffix,
        )
        return [self._cooler_panel]

    def _extract_prefix(self, item) -> None:
        """Extract prefix and update cooler panel."""
        super()._extract_prefix(item)

        # Update cooler panel prefix if it exists
        if self._cooler_panel is not None:
            self._cooler_panel.set_prefix(self._prefix)

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
