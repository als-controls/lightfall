"""Tests for DeviceSelectionModel and DeviceSelectionFilterProxy."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from lucid.ui.models.device_selection import DeviceSelectionItem


class TestDeviceSelectionItem:
    def test_root_item(self):
        root = DeviceSelectionItem.create_root()
        assert root.name == ""
        assert root.dotted_path == ""
        assert root.parent_item is None
        assert root.child_count() == 0

    def test_append_child(self):
        root = DeviceSelectionItem.create_root()
        child = DeviceSelectionItem(name="motor1", dotted_path="motor1", parent=root)
        root.append_child(child)
        assert root.child_count() == 1
        assert root.child(0) is child
        assert child.parent_item is root
        assert child.row() == 0

    def test_dotted_path_for_nested(self):
        root = DeviceSelectionItem.create_root()
        device = DeviceSelectionItem(name="motor1", dotted_path="motor1", parent=root)
        root.append_child(device)
        signal = DeviceSelectionItem(name="readback", dotted_path="motor1.readback", parent=device)
        device.append_child(signal)
        assert signal.dotted_path == "motor1.readback"
        assert signal.row() == 0

    def test_check_state_default_unchecked(self):
        item = DeviceSelectionItem(name="x", dotted_path="x", parent=None)
        assert item.check_state == Qt.CheckState.Unchecked

    def test_check_state_toggle(self):
        root = DeviceSelectionItem.create_root()
        parent = DeviceSelectionItem(name="dev", dotted_path="dev", parent=root)
        child = DeviceSelectionItem(name="sig", dotted_path="dev.sig", parent=parent)
        root.append_child(parent)
        parent.append_child(child)
        child.check_state = Qt.CheckState.Checked
        assert child.check_state == Qt.CheckState.Checked
        assert parent.check_state == Qt.CheckState.Unchecked

    def test_is_writable_default(self):
        item = DeviceSelectionItem(name="x", dotted_path="x", parent=None)
        assert item.is_writable is False

    def test_metadata_dict(self):
        from lucid.devices.model import DeviceCategory
        item = DeviceSelectionItem(
            name="motor1", dotted_path="motor1", parent=None,
            category=DeviceCategory.MOTOR, is_writable=True, kind="hinted",
        )
        d = item.metadata_dict()
        assert d["name"] == "motor1"
        assert d["dotted_path"] == "motor1"
        assert d["category"] == DeviceCategory.MOTOR
        assert d["is_writable"] is True
        assert d["kind"] == "hinted"
        assert d["device_info"] is None
