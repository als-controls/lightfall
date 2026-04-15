"""Tests for CompactMotorWidget."""

from unittest.mock import MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from lucid.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def mock_device_info():
    device_id = uuid4()
    info = MagicMock(spec=DeviceInfo)
    info.id = device_id
    info.name = "test_motor"
    info.device_class = "ophyd.sim.SynAxis"
    info.category = DeviceCategory.MOTOR
    info.metadata = {"units": "mm", "precision": 3}
    info.active = True
    info._state = DeviceState(
        device_id=device_id, status=DeviceStatus.ONLINE, connected=True
    )
    info._ophyd_device = None
    return info


@pytest.fixture
def mock_motor():
    motor = MagicMock()
    motor.name = "test_motor"
    motor.position = 10.0
    motor.moving = False
    motor.user_readback = MagicMock()
    motor.user_setpoint = MagicMock()
    motor.set = MagicMock()
    motor.stop = MagicMock()
    return motor


class TestCompactMotorWidget:
    def test_creation(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget
        widget = CompactMotorWidget(device_info=mock_device_info, ophyd_obj=mock_motor)
        assert widget is not None
        assert widget._name_label.text() == "test_motor"
        widget.close()

    def test_jog_abs_toggle(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget
        widget = CompactMotorWidget(device_info=mock_device_info, ophyd_obj=mock_motor)
        assert widget._mode_btn.text() == "Abs"
        assert widget.is_jog_mode is False
        widget._mode_btn.click()
        assert widget._mode_btn.text() == "Jog"
        assert widget.is_jog_mode is True
        widget._mode_btn.click()
        assert widget._mode_btn.text() == "Abs"
        assert widget.is_jog_mode is False
        widget.close()

    def test_abs_move(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget
        widget = CompactMotorWidget(device_info=mock_device_info, ophyd_obj=mock_motor)
        widget._setpoint_edit.setText("25.0")
        widget._go_btn.click()
        mock_motor.set.assert_called_once_with(25.0)
        widget.close()

    def test_jog_move(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget
        widget = CompactMotorWidget(device_info=mock_device_info, ophyd_obj=mock_motor)
        widget._mode_btn.click()
        assert widget.is_jog_mode is True
        widget._setpoint_edit.setText("5.0")
        widget._go_btn.click()
        mock_motor.set.assert_called_once_with(15.0)
        widget.close()

    def test_stop(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget
        widget = CompactMotorWidget(device_info=mock_device_info, ophyd_obj=mock_motor)
        widget._stop_btn.click()
        mock_motor.stop.assert_called_once()
        widget.close()

    def test_no_motor_shows_connecting(self, qapp, mock_device_info):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget
        mock_device_info._state.status = DeviceStatus.CONNECTING
        widget = CompactMotorWidget(device_info=mock_device_info, ophyd_obj=None)
        assert widget._go_btn.isEnabled() is False
        assert widget._stop_btn.isEnabled() is False
        widget.close()
