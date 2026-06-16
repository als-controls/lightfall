"""Tests for the `device_backend` plugin type loader branch."""
from __future__ import annotations

import pytest

from lightfall.devices.base import DeviceBackend
from lightfall.devices.catalog import DeviceCatalog
from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin
from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.manifest import PluginEntry, PluginManifest


class _StubBackend(DeviceBackend):
    @property
    def name(self): return "stub_backend"
    @property
    def is_connected(self): return False
    @property
    def is_editable(self): return False
    def connect(self): return True
    def disconnect(self): pass
    def get_device(self, device_id): return None
    def get_device_by_name(self, name): return None
    def get_device_by_prefix(self, prefix): return None
    def list_devices(self, category=None, beamline=None, active_only=True): return []
    def search_devices(self, query): return []
    def add_device(self, device): return False
    def update_device(self, device): return False
    def remove_device(self, device_id): return False
    def get_device_configurations(self, device_id): return []
    def get_configuration(self, device_id, config_name): return None
    def save_configuration(self, config): return False
    def delete_configuration(self, config_id): return False
    def get_maintenance_history(self, device_id, limit=100): return []
    def add_maintenance_record(self, record): return False


class _StubBackendPlugin(DeviceBackendPlugin):
    @property
    def name(self): return "stub"
    def create_backend(self): return _StubBackend()


@pytest.fixture
def fresh_catalog(monkeypatch):
    catalog = DeviceCatalog()
    monkeypatch.setattr(DeviceCatalog, "get_instance", classmethod(lambda cls: catalog))
    return catalog


def _patch_pref(monkeypatch, enabled):
    class _Prefs:
        def get(self, key, default=None):
            if key == "device_plugin_stub_enabled":
                return enabled
            return default
    from lightfall.ui.preferences import manager as prefs_mod
    monkeypatch.setattr(prefs_mod.PreferencesManager, "get_instance", classmethod(lambda cls: _Prefs()))


def _manifest():
    return PluginManifest(
        name="test_pkg", version="0.0.0", description="",
        plugins=[PluginEntry(
            type_name="device_backend", name="stub",
            import_path=f"{__name__}:_StubBackendPlugin",
        )],
    )


def test_enabled_plugin_adds_backend_to_catalog(fresh_catalog, monkeypatch):
    _patch_pref(monkeypatch, True)
    loader = PluginLoader()
    loader.register_plugin_type("device_backend", DeviceBackendPlugin)
    loader.load_manifest(_manifest())
    successful, failed = loader.load_all_sync()
    assert successful == 1 and failed == 0
    assert "stub_backend" in fresh_catalog.backends


def test_disabled_plugin_does_not_add_backend(fresh_catalog, monkeypatch):
    _patch_pref(monkeypatch, False)
    loader = PluginLoader()
    loader.register_plugin_type("device_backend", DeviceBackendPlugin)
    loader.load_manifest(_manifest())
    loader.load_all_sync()
    assert "stub_backend" not in fresh_catalog.backends
