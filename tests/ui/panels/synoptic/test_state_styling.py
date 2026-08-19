"""Tests for live device-state styling in the synoptic view."""
import pytest

from lightfall.devices.model import DeviceStatus
from lightfall.ui.panels.synoptic.items import Device2DItem
from lightfall.ui.panels.synoptic.models import DeviceSynopticData


@pytest.fixture
def item():
    return Device2DItem("d1", "motor", DeviceSynopticData(position=(1, 0, 0)))


def test_default_status_is_none(item):
    assert item.get_device_status() is None


def test_set_status_stores_and_flags_update(item):
    item.set_device_status(DeviceStatus.ERROR)
    assert item.get_device_status() == DeviceStatus.ERROR


def test_offline_dims_fill(item):
    item.set_device_status(DeviceStatus.OFFLINE)
    fill, edge_pen = item._effective_style()
    assert fill.alphaF() < 0.5


def test_error_gets_red_edge(item):
    item.set_device_status(DeviceStatus.ERROR)
    fill, edge_pen = item._effective_style()
    assert edge_pen.color().red() > 200
    assert edge_pen.color().green() < 100


def test_selection_overrides_status_edge(item):
    item.set_device_status(DeviceStatus.ERROR)
    item.set_selected(True)
    fill, edge_pen = item._effective_style()
    assert edge_pen.color() == Device2DItem.HIGHLIGHT_COLOR


def test_connecting_gets_amber_edge(item):
    item.set_device_status(DeviceStatus.CONNECTING)
    fill, edge_pen = item._effective_style()
    assert edge_pen.color() == item.STATUS_EDGE_COLORS["connecting"][0]


def test_maintenance_gets_purple_edge(item):
    item.set_device_status(DeviceStatus.MAINTENANCE)
    fill, edge_pen = item._effective_style()
    assert edge_pen.color() == item.STATUS_EDGE_COLORS["maintenance"][0]


def test_panel_routes_state_change_to_item(qtbot):
    """device_state_changed(device_id, state) restyles the matching item."""
    from lightfall.devices.model import DeviceState, DeviceStatus
    from lightfall.ui.panels.synoptic.panel import SynopticPanel
    from uuid import uuid4

    panel = SynopticPanel()
    qtbot.addWidget(panel)

    # Inject a device item directly
    from lightfall.ui.panels.synoptic.models import DeviceSynopticData
    device_id = str(uuid4())
    item = Device2DItem(device_id, "motor", DeviceSynopticData(position=(1, 0, 0)))
    panel._device_items[device_id] = item
    panel._view.add_device_item(device_id, item)

    state = DeviceState(device_id=uuid4(), status=DeviceStatus.ERROR)
    panel._on_device_state_changed(device_id, state)
    assert item.get_device_status() == DeviceStatus.ERROR


def test_add_device_to_view_seeds_initial_status_from_device_state(qtbot):
    """_add_device_to_view() should seed the item's status from
    device_info.state immediately, not just on a later state-change signal."""
    from uuid import uuid4

    from lightfall.devices.model import DeviceInfo, DeviceState, DeviceStatus
    from lightfall.ui.panels.synoptic.panel import SynopticPanel

    panel = SynopticPanel()
    qtbot.addWidget(panel)

    device_info = DeviceInfo(
        id=uuid4(),
        name="motor1",
        metadata={"synoptic": {"position": [1.0, 0.0, 0.0], "visible": True}},
    )
    device_info.state = DeviceState(device_id=device_info.id, status=DeviceStatus.MAINTENANCE)

    panel._add_device_to_view(device_info)

    item = panel._device_items[str(device_info.id)]
    assert item.get_device_status() == DeviceStatus.MAINTENANCE
