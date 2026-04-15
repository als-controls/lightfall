"""Tests for the tabbed DevicePanel."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication, QTabBar, QTabWidget

from lucid.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus
from lucid.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _make_catalog():
    catalog = MagicMock()
    device_id = uuid4()
    info = MagicMock(spec=DeviceInfo)
    info.id = device_id
    info.name = "test_motor"
    info.device_class = "ophyd.sim.SynAxis"
    info.category = DeviceCategory.MOTOR
    info.metadata = {}
    info.active = True
    info._state = DeviceState(
        device_id=device_id, status=DeviceStatus.ONLINE, connected=True
    )
    info._ophyd_device = MagicMock()
    info._ophyd_device.name = "test_motor"
    info._ophyd_device.position = 0.0
    info._ophyd_device.user_readback = MagicMock()
    catalog.get_all_devices.return_value = [info]
    catalog.get_device.return_value = info
    return catalog, info


@pytest.fixture
def prefs_manager():
    mgr = MagicMock()
    mgr.get.return_value = []
    return mgr


class TestDevicePanelTabs:
    def test_has_tab_widget(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel
        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()
        assert isinstance(panel._tabs, QTabWidget)
        assert panel._tabs.count() >= 2
        assert panel._tabs.tabText(0) == "Favorites"
        assert panel._tabs.tabText(1) == "All"
        panel.close()

    def test_first_two_tabs_unclosable(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel
        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()
        tab_bar = panel._tabs.tabBar()
        assert tab_bar.tabButton(0, QTabBar.ButtonPosition.RightSide) is None
        assert tab_bar.tabButton(1, QTabBar.ButtonPosition.RightSide) is None
        panel.close()

    def test_open_device_tab(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel
        from lucid.ui.widgets.device_control import DeviceControlWidget
        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"), \
             patch.object(DeviceControlWidget, "set_items"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()
            item = MagicMock(spec=DeviceTreeItem)
            item.name = "test_motor"
            item.node_type = NodeType.DEVICE
            item.device_info = info
            item.ophyd_obj = info._ophyd_device
            initial_count = panel._tabs.count()
            panel._open_device_tab(item)
            assert panel._tabs.count() == initial_count + 1
            assert panel._tabs.tabText(panel._tabs.count() - 1) == "test_motor"
        panel.close()

    def test_open_device_tab_focuses_existing(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel
        from lucid.ui.widgets.device_control import DeviceControlWidget
        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"), \
             patch.object(DeviceControlWidget, "set_items"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()
            item = MagicMock(spec=DeviceTreeItem)
            item.name = "test_motor"
            item.node_type = NodeType.DEVICE
            item.device_info = info
            item.ophyd_obj = info._ophyd_device
            panel._open_device_tab(item)
            count_after_first = panel._tabs.count()
            panel._open_device_tab(item)
            assert panel._tabs.count() == count_after_first
        panel.close()

    def test_close_device_tab(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel
        from lucid.ui.widgets.device_control import DeviceControlWidget
        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"), \
             patch.object(DeviceControlWidget, "set_items"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()
            item = MagicMock(spec=DeviceTreeItem)
            item.name = "test_motor"
            item.node_type = NodeType.DEVICE
            item.device_info = info
            item.ophyd_obj = info._ophyd_device
            panel._open_device_tab(item)
            device_id = str(info.id)
            assert device_id in panel._device_tabs
            tab_index = panel._tabs.indexOf(panel._device_tabs[device_id])
            panel._on_tab_close_requested(tab_index)
            assert device_id not in panel._device_tabs
        panel.close()
