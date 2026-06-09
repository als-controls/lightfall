"""Tests for the simple_acquire plan, focused on image_mode selection."""

from __future__ import annotations

from lightfall.acquire.plans.lightfall_plans import simple_acquire
from lightfall.devices.sim.areadetector import SimDetector


# EPICS AreaDetector ImageMode enum
SINGLE = 0
MULTIPLE = 1


def _image_mode_set_value(num_images: int) -> int:
    """Run simple_acquire and return the value it sets cam.image_mode to."""
    det = SimDetector(name="sim_det")
    image_mode_obj = det.cam.image_mode
    value = None
    for msg in simple_acquire(detector=det, num_images=num_images):
        if msg.command == "set" and msg.obj is image_mode_obj:
            value = msg.args[0]
    assert value is not None, "simple_acquire never set cam.image_mode"
    return value


def test_single_image_uses_single_mode():
    assert _image_mode_set_value(num_images=1) == SINGLE


def test_multiple_images_uses_multiple_mode():
    assert _image_mode_set_value(num_images=5) == MULTIPLE


def test_open_run_advertises_detector():
    """The start doc must carry detectors so consumers (e.g. the XPCS panel)
    can pick the active detector — open_run does not add it automatically."""
    det = SimDetector(name="sim_det")
    md = None
    for msg in simple_acquire(detector=det, num_images=1):
        if msg.command == "open_run":
            md = msg.kwargs
            break
    assert md is not None and md.get("detectors") == ["sim_det"]
