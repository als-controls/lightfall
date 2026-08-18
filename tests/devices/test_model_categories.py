"""Tests for extended DeviceCategory enum."""
from lightfall.devices.model import DeviceCategory


def test_new_categories_exist():
    assert DeviceCategory.SENSOR == "sensor"
    assert DeviceCategory.SHUTTER == "shutter"
    assert DeviceCategory.VALVE == "valve"


def test_new_categories_have_synoptic_defaults():
    from lightfall.ui.panels.synoptic.models import (
        DEFAULT_COLORS,
        DEFAULT_SHAPES,
        PrimitiveShape,
    )

    for cat in (DeviceCategory.SENSOR, DeviceCategory.SHUTTER, DeviceCategory.VALVE):
        assert cat.value in DEFAULT_SHAPES
        assert cat.value in DEFAULT_COLORS

    assert DEFAULT_SHAPES["shutter"] == PrimitiveShape.SQUARE
    assert DEFAULT_SHAPES["valve"] == PrimitiveShape.SQUARE
    assert DEFAULT_COLORS["shutter"] == (0.9, 0.75, 0.2, 1.0)
    assert DEFAULT_COLORS["valve"] == (0.2, 0.7, 0.8, 1.0)
