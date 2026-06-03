"""Tests for DeviceTreeModel row insertion ordering."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication

from lightfall.devices.model import DeviceInfo, DeviceState, DeviceStatus
from lightfall.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def model_no_timer(qapp):
    """Create a DeviceTreeModel with the background value timer disabled."""
    catalog, device_info, device_id_str = _make_catalog_with_device()
    with patch.object(DeviceTreeModel, "_poll_value_refresh"):
        model = DeviceTreeModel(catalog)
        model._value_timer.stop()
    yield model, catalog, device_info, device_id_str
    model._value_timer.stop()
    model._value_pool.shutdown(wait=False)


def _make_catalog_with_device():
    """Create a mock catalog with one device that has components."""
    catalog = MagicMock()

    device_id = uuid4()
    device_info = MagicMock(spec=DeviceInfo)
    device_info.id = device_id
    device_info.name = "test_motor"
    device_info.device_class = "ophyd.sim.SynAxis"
    device_info.category = MagicMock()
    device_info.category.value = "motor"
    device_info.metadata = {}
    device_info.active = True

    # Initially not connected
    device_info.ophyd_device = None
    device_info._state = DeviceState(
        device_id=device_id, status=DeviceStatus.CONNECTING, connected=False
    )

    catalog.get_all_devices.return_value = [device_info]
    catalog.get_device.return_value = device_info

    return catalog, device_info, str(device_id)


class TestOnDeviceConnected:
    def test_no_blank_rows_after_connect(self, model_no_timer):
        """Children added by _on_device_connected should not produce blank rows.

        The Qt model must call beginInsertRows BEFORE mutating
        item.children, and endInsertRows AFTER.
        """
        model, catalog, device_info, device_id_str = model_no_timer

        # Verify device is in the model
        assert model.rowCount(QModelIndex()) == 1
        device_index = model.index(0, 0)
        assert model.data(device_index) == "test_motor"

        # No children yet
        assert model.rowCount(device_index) == 0

        # Simulate device connecting: create a mock ophyd device with components
        ophyd_device = MagicMock()
        ophyd_device.component_names = ("readback", "setpoint")
        ophyd_device._signals = {
            "readback": MagicMock(),
            "setpoint": MagicMock(),
        }
        ophyd_device._sig_attrs = {}
        device_info.ophyd_device = ophyd_device

        # Track beginInsertRows/endInsertRows calls
        insert_calls = []
        orig_begin = model.beginInsertRows
        orig_end = model.endInsertRows

        def tracking_begin(*args):
            insert_calls.append(("begin", len(model._root.children[0].children)))
            return orig_begin(*args)

        def tracking_end():
            insert_calls.append(("end", len(model._root.children[0].children)))
            return orig_end()

        model.beginInsertRows = tracking_begin
        model.endInsertRows = tracking_end

        # Fire the connected signal handler
        model._on_device_connected(device_id_str)

        # Children should now exist
        assert model.rowCount(device_index) == 2

        # Verify beginInsertRows was called BEFORE children were attached
        # (child count should be 0 at begin time, 2 at end time)
        assert len(insert_calls) == 2
        assert insert_calls[0] == ("begin", 0), (
            "beginInsertRows must be called before children are attached"
        )
        assert insert_calls[1] == ("end", 2), (
            "endInsertRows must be called after children are attached"
        )


class TestRefreshRepopulatesChildren:
    def test_refresh_keeps_subitems_for_connected_devices(self, qapp):
        """After refresh(), connected devices must still expose their
        sub-items. Regression: the model previously left children empty
        on every populate and relied on device_connected to fill them,
        which never re-fires on refresh.
        """
        catalog, device_info, device_id_str = _make_catalog_with_device()

        # Pretend the device is already connected with components.
        ophyd_device = MagicMock()
        ophyd_device.component_names = ("readback", "setpoint")
        ophyd_device._signals = {
            "readback": MagicMock(),
            "setpoint": MagicMock(),
        }
        ophyd_device._sig_attrs = {}
        device_info.ophyd_device = ophyd_device

        with patch.object(DeviceTreeModel, "_poll_value_refresh"):
            model = DeviceTreeModel(catalog)
            model._value_timer.stop()
        try:
            # After initial populate, children should already be present
            # because the device is connected.
            device_index = model.index(0, 0)
            assert model.rowCount(device_index) == 2

            # Now call refresh — children should still be present.
            model.refresh()
            device_index = model.index(0, 0)
            assert model.rowCount(device_index) == 2, (
                "refresh() must repopulate sub-items for already-connected devices"
            )
        finally:
            model._value_pool.shutdown(wait=False)
