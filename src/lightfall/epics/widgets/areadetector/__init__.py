"""
AreaDetector widgets for EPICS PySide6.

Provides widgets for live image viewing and acquisition controls
for EPICS AreaDetector devices.
"""

from lightfall.epics.widgets.areadetector.areadetector import PVAreaDetector
from lightfall.epics.widgets.areadetector.controls import PVAreaDetectorControls
from lightfall.epics.widgets.areadetector.image_view import PVImageView

__all__ = [
    "PVImageView",
    "PVAreaDetectorControls",
    "PVAreaDetector",
]
