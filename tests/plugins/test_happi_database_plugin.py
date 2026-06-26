"""HappiDatabasePlugin resolves a packaged/filesystem happi DB and vends a HappiBackend."""
import json

import pytest

from lightfall.devices.backends.happi import HappiBackend
from lightfall.plugins.happi_database_plugin import HappiDatabasePlugin


def _plugin_for_path(path_value, *, beamline=None, instantiate="background"):
    class _P(HappiDatabasePlugin):
        database_resource = path_value
        # class attrs set below per-instance via closures
        @property
        def name(self):
            return "test_happi_plugin"
    _P.beamline = beamline
    _P.instantiate = instantiate
    return _P()


def test_database_path_accepts_filesystem_string(tmp_path):
    db = tmp_path / "devices.json"
    db.write_text(json.dumps({}))
    plugin = _plugin_for_path(str(db))
    assert plugin.database_path() == db


def test_database_path_missing_raises(tmp_path):
    plugin = _plugin_for_path(str(tmp_path / "nope.json"))
    with pytest.raises(FileNotFoundError):
        plugin.database_path()


def test_database_path_resolves_packaged_resource():
    # lightfall ships a package we can resolve a known file from: use the
    # plugins package's own __init__.py as a guaranteed-present resource.
    plugin = _plugin_for_path(("lightfall.plugins", "__init__.py"))
    p = plugin.database_path()
    assert p.exists() and p.name == "__init__.py"


def test_create_backend_returns_configured_happi_backend(tmp_path):
    db = tmp_path / "devices.json"
    db.write_text(json.dumps({}))
    plugin = _plugin_for_path(str(db), beamline="7.0.1", instantiate="background")
    backend = plugin.create_backend()
    assert isinstance(backend, HappiBackend)
    # Backend must be named after the plugin so it is keyed uniquely in the
    # DeviceCatalog and does not collide with (overwrite) the base "happi"
    # backend, which would make the base backend's devices vanish.
    assert backend.name == plugin.name == "test_happi_plugin"
    assert backend.name != "happi"
    assert str(db) in str(getattr(backend, "_path"))
    assert getattr(backend, "_beamline") == "7.0.1"


def test_is_device_backend_plugin_subclass():
    from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin
    assert issubclass(HappiDatabasePlugin, DeviceBackendPlugin)
    assert HappiDatabasePlugin.type_name == "device_backend"
