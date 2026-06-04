"""Non-ophyd observer-camera abstractions and widgets.

For ophyd-backed area detectors, use lightfall.ui.widgets.camera instead.
"""
from lightfall.ui.widgets.observers.camera import CameraBase
from lightfall.ui.widgets.observers.image_view import CameraImageView

__all__ = ["CameraBase", "CameraImageView"]
