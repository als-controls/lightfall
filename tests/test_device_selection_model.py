"""Tests for DeviceSelectionModel and DeviceSelectionFilterProxy."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QModelIndex, Qt

from lucid.devices.model import DeviceCategory, DeviceInfo
from lucid.ui.models.device_selection import DeviceSelectionItem, DeviceSelectionModel


def _make_device_info(name, category=DeviceCategory.MOTOR):
    from ophyd.sim import SynAxis, SynSignal
    if category == DeviceCategory.MOTOR:
        ophyd_dev = SynAxis(name=name)
    else:
        ophyd_dev = SynSignal(name=name, func=lambda: 1.0)
    info = DeviceInfo(name=name, category=category)
    info._ophyd_device = ophyd_dev
    return info


def _make_catalog(devices):
    catalog = MagicMock()
    catalog.get_all_devices.return_value = devices
    return catalog


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


class TestDeviceSelectionModel:
    def test_empty_catalog(self, qapp):
        catalog = _make_catalog([])
        model = DeviceSelectionModel(catalog)
        assert model.rowCount(QModelIndex()) == 0

    def test_flat_mode_no_children(self, qapp):
        info = _make_device_info("motor1")
        catalog = _make_catalog([info])
        model = DeviceSelectionModel(catalog, show_tree=False)
        assert model.rowCount(QModelIndex()) == 1
        device_index = model.index(0, 0, QModelIndex())
        # Flat mode: no children
        assert model.rowCount(device_index) == 0

    def test_tree_mode_has_children(self, qapp):
        info = _make_device_info("motor1")
        catalog = _make_catalog([info])
        model = DeviceSelectionModel(catalog, show_tree=True)
        assert model.rowCount(QModelIndex()) == 1
        device_index = model.index(0, 0, QModelIndex())
        # SynAxis has readback, setpoint, velocity, acceleration, unused
        assert model.rowCount(device_index) >= 2

    def test_dotted_path_role(self, qapp):
        info = _make_device_info("motor1")
        catalog = _make_catalog([info])
        model = DeviceSelectionModel(catalog, show_tree=True)
        device_index = model.index(0, 0, QModelIndex())
        dotted = model.data(device_index, DeviceSelectionModel.DottedPathRole)
        assert dotted == "motor1"

        # Check a child has dotted path like "motor1.xxx"
        child_index = model.index(0, 0, device_index)
        child_dotted = model.data(child_index, DeviceSelectionModel.DottedPathRole)
        assert child_dotted.startswith("motor1.")

    def test_check_state_independent(self, qapp):
        info = _make_device_info("motor1")
        catalog = _make_catalog([info])
        model = DeviceSelectionModel(catalog, show_tree=True)
        device_index = model.index(0, 0, QModelIndex())
        child_index = model.index(0, 0, device_index)

        # Check child
        model.setData(child_index, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)

        # Child is checked
        assert model.data(child_index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        # Parent is NOT automatically checked
        assert model.data(device_index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked

    def test_get_checked_paths(self, qapp):
        info = _make_device_info("motor1")
        catalog = _make_catalog([info])
        model = DeviceSelectionModel(catalog, show_tree=True)
        device_index = model.index(0, 0, QModelIndex())
        child_index = model.index(0, 0, device_index)

        # Nothing checked yet
        assert model.get_checked_paths() == []

        model.setData(child_index, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        paths = model.get_checked_paths()
        assert len(paths) == 1
        assert paths[0].startswith("motor1.")

    def test_set_checked_paths(self, qapp):
        info = _make_device_info("motor1")
        catalog = _make_catalog([info])
        model = DeviceSelectionModel(catalog, show_tree=True)
        device_index = model.index(0, 0, QModelIndex())

        # Get a child's dotted path
        child_index = model.index(0, 0, device_index)
        child_path = model.data(child_index, DeviceSelectionModel.DottedPathRole)

        # Set it via set_checked_paths
        model.set_checked_paths([child_path])
        assert model.data(child_index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

        # Clear it
        model.set_checked_paths([])
        assert model.data(child_index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked

    def test_writable_detection(self, qapp):
        info = _make_device_info("motor1")
        catalog = _make_catalog([info])
        model = DeviceSelectionModel(catalog, show_tree=True)
        device_index = model.index(0, 0, QModelIndex())

        # Find readback and setpoint children by dotted path
        child_count = model.rowCount(device_index)
        writability = {}
        for i in range(child_count):
            idx = model.index(i, 0, device_index)
            path = model.data(idx, DeviceSelectionModel.DottedPathRole)
            meta = model.data(idx, DeviceSelectionModel.MetadataDictRole)
            name = path.split(".")[-1]
            writability[name] = meta["is_writable"]

        # readback is read-only
        assert writability.get("readback") is False
        # setpoint is writable
        assert writability.get("setpoint") is True
