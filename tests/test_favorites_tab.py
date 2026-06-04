"""Tests for FavoritesTab.

Favorites are identified by device NAME (stable across sessions), not
the runtime UUID, so all calls here use the name as the handle.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

from lightfall.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus


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
    # pydantic v2 fields are not visible to MagicMock(spec=...), so every
    # field the widgets touch must be assigned explicitly.
    info.display_name = ""
    info.icon_override = ""
    info.group = ""
    info._state = DeviceState(
        device_id=device_id, status=DeviceStatus.ONLINE, connected=True
    )
    info._ophyd_device = MagicMock()
    info._ophyd_device.name = name
    info._ophyd_device.position = 0.0
    info._ophyd_device.user_readback = MagicMock()
    return info


def _catalog_with(*infos):
    """Build a MagicMock catalog whose get_device_by_name + get_device
    return the supplied DeviceInfo objects (and None for unknowns)."""
    by_name = {i.name: i for i in infos}
    by_id = {str(i.id): i for i in infos}
    catalog = MagicMock()
    catalog.get_device_by_name.side_effect = by_name.get
    catalog.get_device.side_effect = lambda did: by_id.get(str(did))
    return catalog


class TestFavoritesTab:
    def test_empty_state(self, qapp):
        from lightfall.ui.widgets.favorites_tab import FavoritesTab
        tab = FavoritesTab(catalog=_catalog_with())
        tab.show()
        assert tab._placeholder.isVisible()
        assert len(tab._widgets) == 0
        tab.close()

    def test_add_favorite(self, qapp):
        from lightfall.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("motor_1")
        tab = FavoritesTab(catalog=_catalog_with(info))
        tab.show()
        tab.add_favorite(info.name)
        assert len(tab._widgets) == 1
        assert info.name in tab._widgets
        assert tab._placeholder.isVisible() is False
        tab.close()

    def test_remove_favorite(self, qapp):
        from lightfall.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("motor_1")
        tab = FavoritesTab(catalog=_catalog_with(info))
        tab.show()
        tab.add_favorite(info.name)
        assert len(tab._widgets) == 1
        tab.remove_favorite(info.name)
        assert len(tab._widgets) == 0
        assert tab._placeholder.isVisible()
        tab.close()

    def test_is_favorite(self, qapp):
        from lightfall.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("motor_1")
        tab = FavoritesTab(catalog=_catalog_with(info))
        assert tab.is_favorite(info.name) is False
        tab.add_favorite(info.name)
        assert tab.is_favorite(info.name) is True
        tab.remove_favorite(info.name)
        assert tab.is_favorite(info.name) is False
        tab.close()

    def test_duplicate_add_is_noop(self, qapp):
        from lightfall.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("motor_1")
        tab = FavoritesTab(catalog=_catalog_with(info))
        tab.add_favorite(info.name)
        tab.add_favorite(info.name)
        assert len(tab._widgets) == 1
        tab.close()

    def test_get_favorite_ids(self, qapp):
        from lightfall.ui.widgets.favorites_tab import FavoritesTab
        info1 = _make_motor_info("motor_1")
        info2 = _make_motor_info("motor_2")
        tab = FavoritesTab(catalog=_catalog_with(info1, info2))
        tab.add_favorite(info1.name)
        tab.add_favorite(info2.name)
        ids = tab.get_favorite_ids()
        assert ids == [info1.name, info2.name]
        tab.close()

    def test_pending_favorite_renders_when_device_added(self, qapp):
        """Favorite added before its device exists in the catalog stays
        pending; once the catalog gets the device, on_device_added
        creates the widget."""
        from lightfall.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("late_motor")
        catalog = _catalog_with()  # device not in catalog yet
        tab = FavoritesTab(catalog=catalog)

        tab.set_favorites([info.name])
        # Favorite is recorded, but no widget yet — placeholder hidden
        # since a favorite exists (it just hasn't loaded).
        assert info.name in tab._favorite_ids
        assert info.name not in tab._widgets

        # Device arrives — catalog now knows about it, signal fires.
        catalog.get_device_by_name.side_effect = {info.name: info}.get
        tab.on_device_added(info)
        assert info.name in tab._widgets
        tab.close()

    def test_set_favorites_drops_unknown_then_picks_up_on_add(self, qapp):
        """A name that never resolves stays in _favorite_ids without a
        widget; later add for a different unrelated name still works."""
        from lightfall.ui.widgets.favorites_tab import FavoritesTab
        info = _make_motor_info("good_motor")
        tab = FavoritesTab(catalog=_catalog_with(info))
        tab.set_favorites(["ghost_motor", info.name])
        assert tab._favorite_ids == ["ghost_motor", info.name]
        assert list(tab._widgets) == [info.name]
        tab.close()
