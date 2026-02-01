"""Camera control widgets for EPICS AreaDetector devices.

Provides specialized control UIs for camera devices with:
- Live image display via PVImageView
- Acquisition controls (exposure, images, modes)
- Device-specific panels (cooler, temperature)

Widgets:
    CameraControlWidget: Base camera control for any AreaDetector
    AndorCameraControlWidget: Andor cameras with cooler control
    PIMTECameraControlWidget: Princeton PIMTE cameras with temperature display
"""

from lucid.ui.widgets.camera.base import CameraControlWidget, TVModeMixin
from lucid.ui.widgets.camera.andor import AndorCameraControlWidget
from lucid.ui.widgets.camera.pimte import PIMTECameraControlWidget

__all__ = [
    "CameraControlWidget",
    "TVModeMixin",
    "AndorCameraControlWidget",
    "PIMTECameraControlWidget",
]
