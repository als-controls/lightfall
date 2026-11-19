"""Tests for the simple_acquire plan, focused on image_mode selection."""

from __future__ import annotations

import pytest
from ophyd import Component, Device, Signal

from lightfall.acquire.plans.lightfall_plans import simple_acquire
from lightfall.acquire.plans.stubs import (
    IMAGE_MODE_CONTINUOUS,
    IMAGE_MODE_MULTIPLE,
    IMAGE_MODE_SINGLE,
    image_mode_moves,
    set_image_mode,
)
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


# --- enum-aware image mode (ADAndor3 has ('Fixed', 'Continuous')) -------------

class _EnumSignal(Signal):
    """A soft signal advertising enum strings, like a connected EpicsSignal mbbo."""

    def __init__(self, *args, enum_strs=(), **kwargs):
        super().__init__(*args, **kwargs)
        self._enum = tuple(enum_strs)

    @property
    def enum_strs(self):
        return self._enum


def _cam(enum_strs):
    class Cam(Device):
        image_mode = Component(_EnumSignal, value=0, enum_strs=enum_strs)
        num_images = Component(Signal, value=1)

    return Cam(name="cam")


class _Det(Device):
    pass


def _det(enum_strs):
    det = _Det(name="det")
    det.cam = _cam(enum_strs)
    return det


GENERIC = ("Single", "Multiple", "Continuous")
ANDOR3 = ("Fixed", "Continuous")


@pytest.mark.parametrize("mode, expected", [
    (IMAGE_MODE_SINGLE, 0), (IMAGE_MODE_MULTIPLE, 1), (IMAGE_MODE_CONTINUOUS, 2),
    ("Multiple", 1),
])
def test_generic_enum_maps_by_name(mode, expected):
    cam = _cam(GENERIC)
    assert image_mode_moves(cam, mode) == [cam.image_mode, expected]


def test_andor3_multiple_is_fixed_with_count():
    cam = _cam(ANDOR3)
    assert image_mode_moves(cam, IMAGE_MODE_MULTIPLE, num_images=5) == [
        cam.image_mode, 0, cam.num_images, 5]


def test_andor3_single_is_fixed_one_frame():
    cam = _cam(ANDOR3)
    assert image_mode_moves(cam, IMAGE_MODE_SINGLE) == [cam.image_mode, 0, cam.num_images, 1]


def test_andor3_continuous_is_index_1():
    cam = _cam(ANDOR3)
    assert image_mode_moves(cam, IMAGE_MODE_CONTINUOUS) == [cam.image_mode, 1]


def test_unknown_mode_on_driver_raises():
    with pytest.raises(ValueError, match="no 'Multiple'"):
        image_mode_moves(_cam(("Continuous",)), IMAGE_MODE_MULTIPLE)


def test_set_image_mode_uses_each_cameras_enum():
    generic, andor3 = _det(GENERIC), _det(ANDOR3)
    msgs = [m for m in set_image_mode([generic, andor3], IMAGE_MODE_MULTIPLE)
            if m.command == "set"]
    values = {id(m.obj): m.args[0] for m in msgs}
    assert values[id(generic.cam.image_mode)] == 1
    assert values[id(andor3.cam.image_mode)] == 0


def test_simple_acquire_multi_frame_on_andor3_never_continuous():
    det = _det(ANDOR3)
    sets = {id(m.obj): m.args[0] for m in simple_acquire(detector=det, num_images=5)
            if m.command == "set"}
    assert sets[id(det.cam.image_mode)] == 0  # Fixed, not 1 = Continuous
    assert sets[id(det.cam.num_images)] == 5
