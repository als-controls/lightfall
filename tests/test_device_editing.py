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
