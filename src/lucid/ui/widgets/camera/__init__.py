"""Camera control widgets for ophyd AreaDetector devices.

Provides specialized control UIs for camera devices with:
- Live image display via OphydImageView (PyQtGraph-based)
- Acquisition controls (exposure, images, modes)
- Device-specific panels (cooler, temperature)
- TV mode for continuous streaming
- Plan-based acquisition with dark frame support

Widget Hierarchy:
    CameraControlWidget: Base camera control with direct acquisition
        PlanBasedCameraControlWidget: Uses Bluesky plans for acquisition
            AndorCameraControlWidget: Andor cameras with cooler control
            PIMTECameraControlWidget: Princeton PIMTE with temperature display

Mixins:
    TVModeMixin: Adds TV mode (continuous streaming) support
"""

from lucid.ui.widgets.camera.andor import AndorCameraControlWidget
from lucid.ui.widgets.camera.base import CameraControlWidget, TVModeMixin
from lucid.ui.widgets.camera.pimte import PIMTECameraControlWidget
from lucid.ui.widgets.camera.plan_based import PlanBasedCameraControlWidget

__all__ = [
    "CameraControlWidget",
    "TVModeMixin",
    "PlanBasedCameraControlWidget",
    "AndorCameraControlWidget",
    "PIMTECameraControlWidget",
]
