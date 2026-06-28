import pytest

from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin
from lightfall.monitor.registry import MonitorRegistry
from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.registry import PluginRegistry


@pytest.fixture(autouse=True)
def _reset_monitor_registry():
    MonitorRegistry.reset_instance()
    yield
    MonitorRegistry.reset_instance()


class _Feed(MonitorFeed):
    name = "wf"
    def evaluate(self, ctx, window, prior): return None


class _Plugin(MonitorPlugin):
    @property
    def name(self): return "wired"
    @property
    def description(self): return "wired"
    def create_feeds(self): return [_Feed()]


def test_loader_registers_monitor_into_monitor_registry():
    MonitorRegistry.reset_instance()
    loader = PluginLoader(PluginRegistry())
    loader.register_plugin_type("monitor", MonitorPlugin)
    # Simulate a loaded plugin instance going through type-registry dispatch.
    from lightfall.plugins.loader import PluginInfo  # dataclass holding instance + type_name
    info = PluginInfo(name="wired", type_name="monitor", import_path="", instance=_Plugin())
    loader._register_with_type_registry(info)
    assert MonitorRegistry.get_instance().get_plugins()[0].name == "wired"
