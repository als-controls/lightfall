"""Tests for FavoritesTab."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

from lucid.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _make_motor_info(name="motor_1"):
    device_id = uuid4()
    info = MagicMock(spec=DeviceInfo)
    info.id = device_id
    info.name = name
    info.device_class = "ophyd.sim.SynAxis"
    info.category = DeviceCategory.MOTOR
    info.metadata = {"units": "mm", "precision": 3}
    info.active = True
    info._state = DeviceState(
        device_id=device_id, status=DeviceStatus.ONLINE, connected=True
    )
    info._ophyd_device = MagicMock()
    info._ophyd_device.name = name
    info._ophyd_device.position = 0.0
    info._ophyd_device.user_readback = MagicMock()
    return info


@pytest.fixture
def mock_catalog():
    catalog = MagicMock()
    catalog.get_device.return_value = None
    return catalog


class TestFavoritesTab:
    def test_empty_state(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab
        tab = FavoritesTab(catalog=mock_catalog)
        tab.show()
        assert tab._placeholder.isVisible()
        assert len(tab._widgets) == 0
        tab.close()

    def test_add_favorite(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("motor_1")
        mock_catalog.get_device.return_value = info
        tab = FavoritesTab(catalog=mock_catalog)
        tab.show()
        tab.add_favorite(str(info.id))
        assert len(tab._widgets) == 1
        assert tab._placeholder.isVisible() is False
        tab.close()

    def test_remove_favorite(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("motor_1")
        mock_catalog.get_device.return_value = info
        tab = FavoritesTab(catalog=mock_catalog)
        tab.show()
        tab.add_favorite(str(info.id))
        assert len(tab._widgets) == 1
        tab.remove_favorite(str(info.id))
        assert len(tab._widgets) == 0
        assert tab._placeholder.isVisible()
        tab.close()

    def test_is_favorite(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("motor_1")
        mock_catalog.get_device.return_value = info
        tab = FavoritesTab(catalog=mock_catalog)
        assert tab.is_favorite(str(info.id)) is False
        tab.add_favorite(str(info.id))
        assert tab.is_favorite(str(info.id)) is True
        tab.remove_favorite(str(info.id))
        assert tab.is_favorite(str(info.id)) is False
        tab.close()

    def test_duplicate_add_is_noop(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("motor_1")
        mock_catalog.get_device.return_value = info
        tab = FavoritesTab(catalog=mock_catalog)
        tab.add_favorite(str(info.id))
        tab.add_favorite(str(info.id))
        assert len(tab._widgets) == 1
        tab.close()

    def test_get_favorite_ids(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab
        info1 = _make_motor_info("motor_1")
        info2 = _make_motor_info("motor_2")
        def get_device(device_id):
            if str(device_id) == str(info1.id):
                return info1
            if str(device_id) == str(info2.id):
                return info2
            return None
        mock_catalog.get_device.side_effect = get_device
        tab = FavoritesTab(catalog=mock_catalog)
        tab.add_favorite(str(info1.id))
        tab.add_favorite(str(info2.id))
        ids = tab.get_favorite_ids()
        assert ids == [str(info1.id), str(info2.id)]
        tab.close()
