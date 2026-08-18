"""Unit tests for the signal-control device-category acceptance check.

``is_signal_item`` decides whether a device-tree node should be handled
by the generic signal control widget. DETECTOR and SENSOR categories are
both scalar/point-like devices that share this widget (as opposed to
area detectors, which get CameraControlWidget instead). This test only
exercises the plain category check — no Qt widgets are instantiated.
"""
from __future__ import annotations

from lightfall.devices.model import DeviceCategory, DeviceInfo
from lightfall.ui.models.device_tree import DeviceTreeItem, NodeType
from lightfall.ui.widgets.signal_control import is_signal_item


def _device_item(category: DeviceCategory) -> DeviceTreeItem:
    info = DeviceInfo(name="test_device", category=category)
    return DeviceTreeItem(name="test_device", node_type=NodeType.DEVICE, device_info=info)


def test_sensor_category_accepted_like_detector():
    """SENSOR devices are accepted by the same check that accepts DETECTOR."""
    detector_item = _device_item(DeviceCategory.DETECTOR)
    sensor_item = _device_item(DeviceCategory.SENSOR)

    assert is_signal_item(detector_item) is True
    assert is_signal_item(sensor_item) is True


def test_other_categories_not_accepted_without_signal_hint():
    """A device category unrelated to signals is not accepted."""
    motor_item = _device_item(DeviceCategory.MOTOR)
    assert is_signal_item(motor_item) is False
