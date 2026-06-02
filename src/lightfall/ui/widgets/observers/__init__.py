"""Non-ophyd observer-camera abstractions and widgets.

For ophyd-backed area detectors, use lucid.ui.widgets.camera instead.
"""
from lucid.ui.widgets.observers.camera import CameraBase
from lucid.ui.widgets.observers.image_view import CameraImageView

__all__ = ["CameraBase", "CameraImageView"]
