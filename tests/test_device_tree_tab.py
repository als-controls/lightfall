"""Tests for DeviceTreeTab."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from lightfall.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus
from lightfall.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _make_catalog_with_motors():
    catalog = MagicMock()
    devices = []
    for name in ["motor_a", "motor_b"]:
        device_id = uuid4()
        info = MagicMock(spec=DeviceInfo)
        info.id = device_id
        info.name = name
        info.device_class = "ophyd.sim.SynAxis"
        info.category = DeviceCategory.MOTOR
        info.metadata = {}
        info.active = True
        info._state = DeviceState(
            device_id=device_id, status=DeviceStatus.ONLINE, connected=True
        )
        info._ophyd_device = None
        devices.append(info)
    catalog.get_all_devices.return_value = devices
    catalog.get_device.side_effect = lambda did: next(
        (d for d in devices if d.id == did), None
    )
    return catalog, devices


class TestDeviceTreeTab:
    def test_creation(self, qapp):
        from lightfall.ui.widgets.device_tree_tab import DeviceTreeTab
        catalog, devices = _make_catalog_with_motors()
        with patch.object(DeviceTreeModel, "_poll_value_refresh"):
            tab = DeviceTreeTab(catalog=catalog)
            tab._model._value_timer.stop()
        assert tab._tree_view is not None
        assert tab._search_input is not None
        tab.close()

    def test_signals_exist(self, qapp):
        from lightfall.ui.widgets.device_tree_tab import DeviceTreeTab
        catalog, devices = _make_catalog_with_motors()
        with patch.object(DeviceTreeModel, "_poll_value_refresh"):
            tab = DeviceTreeTab(catalog=catalog)
            tab._model._value_timer.stop()
        assert hasattr(tab, "device_open_requested")
        assert hasattr(tab, "favorite_toggled")
        tab.close()

    def test_search_filters_tree(self, qapp):
        from lightfall.ui.widgets.device_tree_tab import DeviceTreeTab
        catalog, devices = _make_catalog_with_motors()
        with patch.object(DeviceTreeModel, "_poll_value_refresh"):
            tab = DeviceTreeTab(catalog=catalog)
            tab._model._value_timer.stop()
        tab._search_input.setText("motor_a")
        assert tab._proxy_model.filterRegularExpression().pattern() == "motor_a"
        tab.close()
