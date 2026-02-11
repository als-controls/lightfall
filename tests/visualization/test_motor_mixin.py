"""Tests for VisualizationMotorMixin.

Tests motor field detection, menu item enable/disable logic,
and coordinate conversion for visualization widgets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QMenu

from lucid.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus
from lucid.visualization.motor_mixin import VisualizationMotorMixin
from lucid.visualization.spec import DataCharacteristics, FieldInfo, VisualizationSpec, VizType


@dataclass
class MockSpec:
    """Mock VisualizationSpec for testing."""

    x_field: str | None = "motor_x"
    y_field: str | None = "motor_y"
    z_field: str | None = "detector"
    characteristics: DataCharacteristics = field(default_factory=lambda: DataCharacteristics(
        ndim=2,
        dim_fields=["motor_x", "motor_y"],
        dep_fields=["detector"],
        field_info={
            "motor_x": FieldInfo(name="motor_x", units="mm"),
            "motor_y": FieldInfo(name="motor_y", units="mm"),
            "detector": FieldInfo(name="detector"),
        },
    ))


class MockPlotWidget:
    """Mock PyQtGraph PlotWidget."""

    def __init__(self):
        self._scene = MagicMock()
        self._plot_item = MagicMock()
        self._vb = MagicMock()
        self._vb.menu = QMenu()
        self._plot_item.vb = self._vb

    def scene(self):
        return self._scene

    def getPlotItem(self):
        return self._plot_item


class TestableMotorMixin(VisualizationMotorMixin):
    """Testable class that uses the mixin."""

    def __init__(self):
        self._spec = MockSpec()
        self._plot_widget = MockPlotWidget()
        # Initialize instance attributes that _setup_motor_context_menu would set
        self._last_data_pos = None
        self._motor_menu_actions = []
        self._motor_menu = None


class TestIsMotorField:
    """Tests for _is_motor_field method."""

    def test_motor_field_in_dim_fields(self):
        """Motor field in dim_fields should return True."""
        mixin = TestableMotorMixin()
        assert mixin._is_motor_field("motor_x") is True
        assert mixin._is_motor_field("motor_y") is True

    def test_detector_field_not_motor(self):
        """Detector field should return False."""
        mixin = TestableMotorMixin()
        assert mixin._is_motor_field("detector") is False

    def test_unknown_field_not_motor(self):
        """Unknown field should return False."""
        mixin = TestableMotorMixin()
        assert mixin._is_motor_field("unknown_field") is False

    def test_none_field_not_motor(self):
        """None field should return False."""
        mixin = TestableMotorMixin()
        assert mixin._is_motor_field(None) is False

    def test_empty_string_not_motor(self):
        """Empty string should return False."""
        mixin = TestableMotorMixin()
        assert mixin._is_motor_field("") is False


class TestGetMotorDevice:
    """Tests for _get_motor_device method."""

    def test_motor_found_in_catalog(self):
        """Should return motor device when found in catalog."""
        mixin = TestableMotorMixin()

        mock_ophyd = MagicMock()
        mock_device_info = DeviceInfo(
            name="motor_x",
            category=DeviceCategory.MOTOR,
        )
        mock_device_info._ophyd_device = mock_ophyd

        with patch("lucid.devices.catalog.DeviceCatalog.get_instance") as mock_catalog:
            mock_catalog.return_value.get_device_by_name.return_value = mock_device_info
            result = mixin._get_motor_device("motor_x")

        assert result is not None
        assert result[0] == mock_ophyd
        assert result[1] == mock_device_info

    def test_motor_not_found(self):
        """Should return None when motor not in catalog."""
        mixin = TestableMotorMixin()

        with patch("lucid.devices.catalog.DeviceCatalog.get_instance") as mock_catalog:
            mock_catalog.return_value.get_device_by_name.return_value = None
            result = mixin._get_motor_device("unknown_motor")

        assert result is None

    def test_device_is_detector_not_motor(self):
        """Should return None when device is not a motor category."""
        mixin = TestableMotorMixin()

        mock_device_info = DeviceInfo(
            name="detector",
            category=DeviceCategory.DETECTOR,
        )

        with patch("lucid.devices.catalog.DeviceCatalog.get_instance") as mock_catalog:
            mock_catalog.return_value.get_device_by_name.return_value = mock_device_info
            result = mixin._get_motor_device("detector")

        assert result is None

    def test_motor_with_no_ophyd_device(self):
        """Should return None when motor has no ophyd device instance."""
        mixin = TestableMotorMixin()

        mock_device_info = DeviceInfo(
            name="motor_x",
            category=DeviceCategory.MOTOR,
        )
        # ophyd_device is None by default

        with patch("lucid.devices.catalog.DeviceCatalog.get_instance") as mock_catalog:
            mock_catalog.return_value.get_device_by_name.return_value = mock_device_info
            result = mixin._get_motor_device("motor_x")

        assert result is None


class TestCanMoveMotor:
    """Tests for _can_move_motor method."""

    def test_can_move_connected_motor_no_scan(self):
        """Should return True for connected motor with no scan running."""
        mixin = TestableMotorMixin()

        mock_ophyd = MagicMock()
        mock_device_info = DeviceInfo(
            name="motor_x",
            category=DeviceCategory.MOTOR,
        )
        mock_device_info._ophyd_device = mock_ophyd
        mock_device_info._state = DeviceState(
            device_id=mock_device_info.id,
            connected=True,
            status=DeviceStatus.ONLINE,
        )

        with patch.object(mixin, "_get_motor_device", return_value=(mock_ophyd, mock_device_info)):
            with patch.object(mixin, "_is_scan_running", return_value=False):
                can_move, reason = mixin._can_move_motor("motor_x")

        assert can_move is True
        assert reason == ""

    def test_cannot_move_during_scan(self):
        """Should return False when scan is running."""
        mixin = TestableMotorMixin()

        mock_ophyd = MagicMock()
        mock_device_info = DeviceInfo(
            name="motor_x",
            category=DeviceCategory.MOTOR,
        )
        mock_device_info._ophyd_device = mock_ophyd
        mock_device_info._state = DeviceState(
            device_id=mock_device_info.id,
            connected=True,
            status=DeviceStatus.ONLINE,
        )

        with patch.object(mixin, "_get_motor_device", return_value=(mock_ophyd, mock_device_info)):
            with patch.object(mixin, "_is_scan_running", return_value=True):
                can_move, reason = mixin._can_move_motor("motor_x")

        assert can_move is False
        assert "scan is running" in reason.lower()

    def test_cannot_move_disconnected_motor(self):
        """Should return False when motor is disconnected."""
        mixin = TestableMotorMixin()

        mock_ophyd = MagicMock()
        mock_device_info = DeviceInfo(
            name="motor_x",
            category=DeviceCategory.MOTOR,
        )
        mock_device_info._ophyd_device = mock_ophyd
        mock_device_info._state = DeviceState(
            device_id=mock_device_info.id,
            connected=False,
            status=DeviceStatus.OFFLINE,
        )

        with patch.object(mixin, "_get_motor_device", return_value=(mock_ophyd, mock_device_info)):
            with patch.object(mixin, "_is_scan_running", return_value=False):
                can_move, reason = mixin._can_move_motor("motor_x")

        assert can_move is False
        assert "not connected" in reason.lower()

    def test_cannot_move_unknown_motor(self):
        """Should return False when motor not found."""
        mixin = TestableMotorMixin()

        with patch.object(mixin, "_get_motor_device", return_value=None):
            can_move, reason = mixin._can_move_motor("unknown_motor")

        assert can_move is False
        assert "not found" in reason.lower()


class TestAddMotorActionsToMenu:
    """Tests for _add_motor_actions_to_menu method."""

    def test_adds_x_action_for_1d_plot(self):
        """Should add only 'Go to X' for 1D plot where only X is motor."""
        mixin = TestableMotorMixin()
        # Set up as 1D - only X is a motor
        mixin._spec.characteristics.dim_fields = ["motor_x"]
        mixin._spec.y_field = "detector"  # Not a motor

        menu = QMenu()
        data_pos = QPointF(5.0, 100.0)

        with patch.object(mixin, "_can_move_motor", return_value=(True, "")):
            mixin._add_motor_actions_to_menu(menu, data_pos)

        # Should have a "Move Motor" submenu
        assert mixin._motor_menu is not None
        actions = mixin._motor_menu.actions()
        assert len(actions) == 1
        assert "motor_x" in actions[0].text()
        assert "5" in actions[0].text()

    def test_adds_xy_actions_for_2d_plot(self):
        """Should add X, Y, and X,Y actions for 2D plot."""
        mixin = TestableMotorMixin()

        menu = QMenu()
        data_pos = QPointF(5.0, 10.0)

        with patch.object(mixin, "_can_move_motor", return_value=(True, "")):
            mixin._add_motor_actions_to_menu(menu, data_pos)

        assert mixin._motor_menu is not None
        actions = [a for a in mixin._motor_menu.actions() if not a.isSeparator()]
        # Should have: Go to X, Go to Y, Go to X,Y
        assert len(actions) == 3

    def test_no_menu_when_no_motors(self):
        """Should not create submenu if no fields are motors."""
        mixin = TestableMotorMixin()
        mixin._spec.characteristics.dim_fields = []  # No dim fields

        menu = QMenu()
        data_pos = QPointF(5.0, 10.0)

        mixin._add_motor_actions_to_menu(menu, data_pos)
        assert mixin._motor_menu is None

    def test_disabled_action_when_cannot_move(self):
        """Should disable action when motor cannot be moved."""
        mixin = TestableMotorMixin()
        mixin._spec.characteristics.dim_fields = ["motor_x"]
        mixin._spec.y_field = "detector"

        menu = QMenu()
        data_pos = QPointF(5.0, 100.0)

        with patch.object(mixin, "_can_move_motor", return_value=(False, "Scan running")):
            mixin._add_motor_actions_to_menu(menu, data_pos)

        assert mixin._motor_menu is not None
        actions = mixin._motor_menu.actions()
        assert not actions[0].isEnabled()


class TestRemoveMotorActionsFromMenu:
    """Tests for _remove_motor_actions_from_menu method."""

    def test_removes_previously_added_actions(self):
        """Should remove motor actions from menu."""
        mixin = TestableMotorMixin()
        menu = QMenu()
        data_pos = QPointF(5.0, 10.0)

        # Add actions first
        with patch.object(mixin, "_can_move_motor", return_value=(True, "")):
            mixin._add_motor_actions_to_menu(menu, data_pos)

        # Verify submenu was added
        assert mixin._motor_menu is not None
        initial_action_count = len(menu.actions())
        assert initial_action_count > 0

        # Remove actions
        mixin._remove_motor_actions_from_menu(menu)

        # Verify submenu was removed
        assert mixin._motor_menu is None
        assert len(mixin._motor_menu_actions) == 0


class TestMousePositionTracking:
    """Tests for mouse position tracking for context menu."""

    def test_on_mouse_moved_updates_last_data_pos(self):
        """Mouse move should update _last_data_pos."""
        mixin = TestableMotorMixin()

        # Mock the viewbox to return a specific data coordinate
        expected_data_pos = QPointF(10.5, 20.3)
        mixin._plot_widget._vb.mapSceneToView.return_value = expected_data_pos

        scene_pos = QPointF(100, 200)
        mixin._on_mouse_moved_for_menu(scene_pos)

        assert mixin._last_data_pos == expected_data_pos


class TestSessionSkipConfirmation:
    """Tests for session-level skip confirmation flag."""

    def test_skip_confirmation_is_class_level(self):
        """Skip confirmation should be shared across instances."""
        # Reset to ensure clean state
        VisualizationMotorMixin._skip_move_confirmation = False

        mixin1 = TestableMotorMixin()
        mixin2 = TestableMotorMixin()

        assert mixin1._skip_move_confirmation is False
        assert mixin2._skip_move_confirmation is False

        # Set via one instance
        VisualizationMotorMixin._skip_move_confirmation = True

        # Should be visible in both
        assert mixin1._skip_move_confirmation is True
        assert mixin2._skip_move_confirmation is True

        # Reset for other tests
        VisualizationMotorMixin._skip_move_confirmation = False


class TestSetupMotorContextMenu:
    """Tests for _setup_motor_context_menu method."""

    def test_connects_to_mouse_moved_signal(self):
        """Should connect to sigMouseMoved for position tracking."""
        mixin = TestableMotorMixin()
        mixin._setup_motor_context_menu()

        # Verify sigMouseMoved was connected
        mixin._plot_widget._scene.sigMouseMoved.connect.assert_called_once()

    def test_connects_to_menu_about_to_show(self):
        """Should connect to menu's aboutToShow signal."""
        mixin = TestableMotorMixin()
        # Need to mock the menu's aboutToShow signal
        mock_menu = MagicMock()
        mixin._plot_widget._vb.menu = mock_menu

        mixin._setup_motor_context_menu()

        # Verify aboutToShow was connected
        mock_menu.aboutToShow.connect.assert_called_once()
