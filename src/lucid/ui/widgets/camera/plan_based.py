"""Plan-based camera control widget.

Extends CameraControlWidget to use Bluesky plans for acquisition instead
of direct detector control. This provides support for:
- Dark frame collection with shutter control
- Proper Bluesky document streaming
- Integration with the RunEngine queue

Use this as a base class for detectors that require plan-based acquisition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtWidgets import QCheckBox, QGroupBox, QWidget

from lucid.ui.widgets.camera.base import CameraControlWidget
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class PlanBasedCameraControlWidget(CameraControlWidget):
    """Camera control widget that uses Bluesky plans for acquisition.

    Instead of directly setting cam.acquire=1, this widget submits
    acquisition plans to the RunEngine. This enables:
    - Automatic dark frame collection
    - Proper Bluesky document streaming
    - Integration with the plan queue
    - Shutter control for dark frames

    Subclasses can override `_create_acquisition_plan()` to customize
    the acquisition workflow for specific detector types.

    Class Attributes:
        collect_dark_default: Default value for collect dark checkbox.
    """

    collect_dark_default: ClassVar[bool] = False

    def __init__(self, parent: QWidget | None = None) -> None:
        self._collect_dark_checkbox: QCheckBox | None = None
        super().__init__(parent)

    def _create_device_panels(self) -> list[QGroupBox]:
        """Create device-specific panels including acquisition options."""
        panels = super()._create_device_panels()

        # Add acquisition options panel
        options_group = QGroupBox("Acquisition Options")
        from PySide6.QtWidgets import QVBoxLayout

        options_layout = QVBoxLayout(options_group)

        self._collect_dark_checkbox = QCheckBox("Collect dark frame")
        self._collect_dark_checkbox.setChecked(self.collect_dark_default)
        self._collect_dark_checkbox.setToolTip(
            "Collect a dark frame (shutter closed) before each acquisition"
        )
        options_layout.addWidget(self._collect_dark_checkbox)

        # Insert at beginning of panels list
        panels.insert(0, options_group)
        return panels

    def _on_acquire_clicked(self) -> None:
        """Start acquisition using a Bluesky plan.

        Submits an acquisition plan to the RunEngine instead of
        directly triggering the detector.
        """
        if self._device is None:
            logger.warning("Cannot acquire: no device selected")
            return

        # Pause TV mode if active
        was_tv_mode = self.is_tv_mode_active()
        if was_tv_mode:
            self.pause_tv_mode()

        # Get acquisition parameters
        collect_dark = (
            self._collect_dark_checkbox.isChecked()
            if self._collect_dark_checkbox is not None
            else False
        )

        # Create and submit plan
        plan = self._create_acquisition_plan(collect_dark=collect_dark)

        try:
            from lucid.acquire.engine import get_engine

            engine = get_engine()

            # Define completion callback to resume TV mode
            def on_complete(success: bool) -> None:
                if was_tv_mode and success:
                    self.resume_tv_mode()

            # Submit plan to engine
            engine.submit(plan, on_complete=on_complete)
            logger.debug(f"Submitted acquisition plan for {self._device.name}")

        except Exception as e:
            logger.error(f"Failed to submit acquisition plan: {e}")
            # Resume TV mode on error
            if was_tv_mode:
                self.resume_tv_mode()

    def _create_acquisition_plan(self, collect_dark: bool = False):
        """Create the acquisition plan.

        Override in subclasses to customize the acquisition workflow.

        Args:
            collect_dark: Whether to collect dark frames.

        Returns:
            A Bluesky plan generator.
        """
        from lucid.acquire.plans.ncs_plans import simple_acquire

        # Get current settings from UI
        try:
            num_images = int(self._num_images_edit.text())
        except ValueError:
            num_images = 1

        try:
            acquire_time = float(self._acquire_time_edit.text())
        except ValueError:
            acquire_time = None

        return simple_acquire(
            detector=self._device,
            num_images=num_images,
            acquire_time=acquire_time,
            collect_dark=collect_dark,
        )

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = super().get_introspection_data()
        data["plan_based"] = True
        data["collect_dark_enabled"] = (
            self._collect_dark_checkbox.isChecked()
            if self._collect_dark_checkbox is not None
            else False
        )
        return data
