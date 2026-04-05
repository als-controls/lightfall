"""Tests for device editing feature."""

from __future__ import annotations

import pytest

from lucid.devices.model import DeviceInfo


class TestDeviceInfoNewFields:
    """Test the new fields on DeviceInfo."""

    def test_default_values(self):
        """New fields should have sensible defaults."""
        device = DeviceInfo(name="test_motor")
        assert device.display_name == ""
        assert device.icon_override == ""
        assert device.group == ""

    def test_set_display_name(self):
        device = DeviceInfo(name="motor1", display_name="Main Motor")
        assert device.display_name == "Main Motor"

    def test_set_icon_override(self):
        device = DeviceInfo(name="motor1", icon_override="star")
        assert device.icon_override == "star"

    def test_set_group(self):
        device = DeviceInfo(name="motor1", group="hutch_a")
        assert device.group == "hutch_a"

    def test_fields_in_summary(self):
        """New fields should appear in to_summary() output."""
        device = DeviceInfo(
            name="motor1",
            display_name="Main Motor",
            group="hutch_a",
        )
        summary = device.to_summary()
        assert summary["display_name"] == "Main Motor"
        assert summary["group"] == "hutch_a"


from lucid.devices.base import DeviceBackend


class TestBackendEditable:
    """Test the is_editable property on backends."""

    def test_base_backend_not_editable(self):
        """DeviceBackend.is_editable should default to False."""
        # We can't instantiate the ABC directly, so test via a concrete subclass
        from lucid.devices.backends.mock import MockBackend

        backend = MockBackend()
        assert backend.is_editable is False


import json
import tempfile
from pathlib import Path

from lucid.devices.model import DeviceCategory, ConnectionType


class TestHappiBackendWriteThrough:
    """Test HappiBackend persistence to JSON."""

    @pytest.fixture
    def happi_json(self, tmp_path):
        """Create a minimal happi JSON database file."""
        db_path = tmp_path / "test_happi.json"
        db_path.write_text(json.dumps({}))
        return str(db_path)

    @pytest.fixture
    def backend(self, happi_json):
        """Create a connected HappiBackend."""
        pytest.importorskip("happi")
        from lucid.devices.backends.happi import HappiBackend

        be = HappiBackend(path=happi_json, instantiate=False)
        be.connect()
        return be

    def test_is_editable(self, backend):
        assert backend.is_editable is True

    def test_add_device_persists(self, backend, happi_json):
        """Adding a device should write to the JSON file."""
        device = DeviceInfo(
            name="new_motor",
            device_class="ophyd.EpicsMotor",
            prefix="NEW:MOTOR:",
            category=DeviceCategory.MOTOR,
            connection_type=ConnectionType.EPICS,
        )
        assert backend.add_device(device) is True
        found = backend.get_device_by_name("new_motor")
        assert found is not None
        assert found.prefix == "NEW:MOTOR:"
        with open(happi_json) as f:
            data = json.load(f)
        assert "new_motor" in str(data)

    def test_update_device_persists(self, backend, happi_json):
        """Updating a device should write changes to the JSON file."""
        device = DeviceInfo(
            name="upd_motor",
            device_class="ophyd.EpicsMotor",
            prefix="UPD:MOTOR:",
            category=DeviceCategory.MOTOR,
        )
        backend.add_device(device)
        device.prefix = "UPD:MOTOR:NEW:"
        device.display_name = "Updated Motor"
        device.group = "hutch_b"
        assert backend.update_device(device) is True
        with open(happi_json) as f:
            data = json.load(f)
        raw = json.dumps(data)
        assert "UPD:MOTOR:NEW:" in raw

    def test_remove_device_persists(self, backend, happi_json):
        """Removing a device should delete it from the JSON file."""
        device = DeviceInfo(
            name="del_motor",
            device_class="ophyd.EpicsMotor",
            prefix="DEL:MOTOR:",
            category=DeviceCategory.MOTOR,
        )
        backend.add_device(device)
        assert backend.remove_device(device.id) is True
        assert backend.get_device_by_name("del_motor") is None
        with open(happi_json) as f:
            data = json.load(f)
        assert "del_motor" not in json.dumps(data)

    def test_update_active_persists(self, backend, happi_json):
        """Toggling active should persist."""
        device = DeviceInfo(
            name="toggle_motor",
            device_class="ophyd.EpicsMotor",
            prefix="TOG:MOTOR:",
        )
        backend.add_device(device)
        device.active = False
        backend.update_device(device)
        from lucid.devices.backends.happi import HappiBackend
        be2 = HappiBackend(path=happi_json, instantiate=False)
        be2.connect()
        found = be2.get_device_by_name("toggle_motor")
        assert found is not None
        assert found.active is False

    def test_auto_init_creates_db(self, tmp_path):
        """If JSON path doesn't exist, auto-create it."""
        pytest.importorskip("happi")
        from lucid.devices.backends.happi import HappiBackend
        db_path = tmp_path / "nonexistent" / "happi.json"
        be = HappiBackend(path=str(db_path), instantiate=False)
        result = be.connect()
        assert result is True
        assert db_path.exists()

    def test_add_duplicate_name_fails(self, backend):
        """Adding a device with a duplicate name should fail."""
        device1 = DeviceInfo(name="dup_motor", device_class="ophyd.EpicsMotor")
        assert backend.add_device(device1) is True
        device2 = DeviceInfo(name="dup_motor", device_class="ophyd.EpicsMotor")
        assert backend.add_device(device2) is False
