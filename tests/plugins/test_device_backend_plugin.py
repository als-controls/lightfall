"""Tests for the DeviceBackendPlugin type."""
from __future__ import annotations

from lightfall.devices.base import DeviceBackend
from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin


class _SampleBackendPlugin(DeviceBackendPlugin):
    @property
    def name(self) -> str:
        return "sample_backend"

    def create_backend(self) -> DeviceBackend:
        raise NotImplementedError  # not needed for type tests


class _NotABackendPlugin:
    pass


def test_type_name_and_singleton():
    assert DeviceBackendPlugin.type_name == "device_backend"
    assert DeviceBackendPlugin.is_singleton is True


def test_validate_class_accepts_subclass():
    assert DeviceBackendPlugin.validate_class(_SampleBackendPlugin) is True


def test_validate_class_rejects_non_subclass():
    assert DeviceBackendPlugin.validate_class(_NotABackendPlugin) is False
