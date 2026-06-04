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

Device-specific camera widgets (Andor, PIMTE, etc.) are provided by
endstation plugin packages (e.g., lightfall-endstation-7011).

Mixins:
    TVModeMixin: Adds TV mode (continuous streaming) support

Panels:
    CoolerPanel: Cooler control for Andor-style cameras
    TemperaturePanel: Temperature display for PIMTE-style cameras
"""

from lightfall.ui.widgets.camera.base import CameraControlWidget, TVModeMixin
from lightfall.ui.widgets.camera.dark_frames import DarkFrameManager
from lightfall.ui.widgets.camera.image_view import OphydImageView
from lightfall.ui.widgets.camera.plan_based import PlanBasedCameraControlWidget

__all__ = [
    "CameraControlWidget",
    "DarkFrameManager",
    "OphydImageView",
    "PlanBasedCameraControlWidget",
    "TVModeMixin",
]
