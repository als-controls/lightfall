"""Tests for extended DeviceCategory enum."""
from lightfall.devices.model import DeviceCategory


def test_new_categories_exist():
    assert DeviceCategory.SENSOR == "sensor"
    assert DeviceCategory.SHUTTER == "shutter"
    assert DeviceCategory.VALVE == "valve"


def test_new_categories_have_synoptic_defaults():
    from lightfall.ui.panels.synoptic.models import DEFAULT_COLORS, DEFAULT_SHAPES

    for cat in (DeviceCategory.SENSOR, DeviceCategory.SHUTTER, DeviceCategory.VALVE):
        assert cat.value in DEFAULT_SHAPES
        assert cat.value in DEFAULT_COLORS
