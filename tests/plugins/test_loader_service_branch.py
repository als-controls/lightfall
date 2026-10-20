"""Tests for the `service` plugin type loader branch."""
from __future__ import annotations

import pytest

from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.manifest import PluginEntry, PluginManifest
from lightfall.plugins.service_plugin import ServicePlugin
from lightfall.services.manager import ServiceManager
from lightfall_utils.ipc.services.descriptor import ServiceDescriptor


class _StubServicePlugin(ServicePlugin):
    @property
    def name(self): return "stub_service"

    def descriptor(self):
        return ServiceDescriptor(
            name="stubsvc", scope="{prefix}.host.{hostname}.{client_instance}",
            contract="1.0", launch="local", command=("-m", "nothing"),
        )


@pytest.fixture
def fresh_manager(qapp, monkeypatch):
    m = ServiceManager()
    monkeypatch.setattr(ServiceManager, "get_instance", classmethod(lambda cls: m))
    yield m
    m.stop()


def _manifest():
    return PluginManifest(
        name="test_pkg", version="0.0.0", description="",
        plugins=[PluginEntry(
            type_name="service", name="stub_service",
            import_path=f"{__name__}:_StubServicePlugin",
        )],
    )


def test_service_plugin_registers_descriptor(fresh_manager):
    loader = PluginLoader()
    loader.register_plugin_type("service", ServicePlugin)
    loader.load_manifest(_manifest())
    successful, failed = loader.load_all_sync()
    assert successful == 1 and failed == 0
    assert [s["name"] for s in fresh_manager.statuses()] == ["stubsvc"]
    # Registration alone does not start a local service.
    assert fresh_manager.client("stubsvc") is None
