"""Tests for the new DeviceSelectorDialog."""
from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from PySide6.QtCore import Qt
from lightfall.devices.model import DeviceCategory, DeviceInfo


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


class TestDeviceSelectorDialog:
    @pytest.fixture
    def catalog(self):
        return _make_catalog([
            _make_device_info("motor1", DeviceCategory.MOTOR),
            _make_device_info("motor2", DeviceCategory.MOTOR),
            _make_device_info("det1", DeviceCategory.DETECTOR),
        ])

    def test_flat_mode_shows_devices(self, qapp, catalog):
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog
        dlg = DeviceSelectorDialog(catalog, show_tree=False)
        assert dlg._proxy.rowCount() == 3

    def test_tree_mode_shows_children(self, qapp, catalog):
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog
        from PySide6.QtCore import Qt
        dlg = DeviceSelectorDialog(catalog, show_tree=True)
        # Find a motor row (det1 sorts before motor* alphabetically)
        motor_idx = None
        for row in range(dlg._proxy.rowCount()):
            idx = dlg._proxy.index(row, 0)
            name = dlg._proxy.data(idx, Qt.ItemDataRole.DisplayRole)
            if name and name.startswith("motor"):
                motor_idx = idx
                break
        assert motor_idx is not None, "No motor found in proxy"
        assert dlg._proxy.rowCount(motor_idx) >= 2

    def test_category_filter(self, qapp, catalog):
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog
        dlg = DeviceSelectorDialog(catalog, categories={DeviceCategory.DETECTOR})
        assert dlg._proxy.rowCount() == 1

    def test_initial_selection(self, qapp, catalog):
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog
        dlg = DeviceSelectorDialog(catalog, initial_selection=["motor1", "det1"])
        paths = dlg.get_selected_paths()
        assert "motor1" in paths
        assert "det1" in paths
        assert "motor2" not in paths

    def test_get_selected_paths_empty(self, qapp, catalog):
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog
        dlg = DeviceSelectorDialog(catalog)
        assert dlg.get_selected_paths() == []

    def test_search_filters_view(self, qapp, catalog):
        from lightfall.ui.widgets.device_selector import DeviceSelectorDialog
        dlg = DeviceSelectorDialog(catalog)
        dlg._search_edit.setText("det")
        assert dlg._proxy.rowCount() == 1


class TestIconResolution:
    def test_explicit_icon(self, qapp):
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name
        assert resolve_button_icon_name(icon="mdi6.engine", categories=None) == "mdi6.engine"

    def test_explicit_icon_no_prefix(self, qapp):
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name
        assert resolve_button_icon_name(icon="engine", categories=None) == "mdi6.engine"

    def test_auto_from_motor_category(self, qapp):
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name
        result = resolve_button_icon_name(icon=None, categories={DeviceCategory.MOTOR})
        assert result == "mdi6.engine"

    def test_auto_from_detector_category(self, qapp):
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name
        result = resolve_button_icon_name(icon=None, categories={DeviceCategory.DETECTOR})
        assert result == "mdi6.camera"

    def test_auto_from_controller_category(self, qapp):
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name
        result = resolve_button_icon_name(icon=None, categories={DeviceCategory.CONTROLLER})
        assert result == "mdi6.tune-variant"

    def test_multi_category_uses_default(self, qapp):
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name
        result = resolve_button_icon_name(icon=None, categories={DeviceCategory.MOTOR, DeviceCategory.DETECTOR})
        assert result == "mdi6.microwave"

    def test_no_icon_no_category(self, qapp):
        from lightfall.ui.widgets.device_selector import resolve_button_icon_name
        assert resolve_button_icon_name(icon=None, categories=None) == "mdi6.microwave"
