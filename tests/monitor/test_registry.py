import pytest
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin
from lightfall.monitor.registry import (
    DISABLED_MONITORS_PREF, MonitorRegistry,
)


class _Feed(MonitorFeed):
    def __init__(self, name): self.name = name
    def evaluate(self, ctx, window, prior): return None


def _plugin(name, enabled=True):
    class _P(MonitorPlugin):
        @property
        def name(self): return name
        @property
        def description(self): return name
        @property
        def enabled_by_default(self): return enabled
        def create_feeds(self): return [_Feed(f"{name}_feed")]
    return _P()


@pytest.fixture(autouse=True)
def _reset():
    MonitorRegistry.reset_instance()
    yield
    MonitorRegistry.reset_instance()


def test_enabled_plugins_respects_opt_out(monkeypatch):
    reg = MonitorRegistry.get_instance()
    reg.register(_plugin("a"))
    reg.register(_plugin("b"))
    # Pretend "a" is user-disabled.
    monkeypatch.setattr(reg, "_read_list_pref",
                        lambda key: ["a"] if key == DISABLED_MONITORS_PREF else [])
    names = [p.name for p in reg.enabled_plugins()]
    assert names == ["b"]


def test_enabled_feeds_flattens(monkeypatch):
    reg = MonitorRegistry.get_instance()
    reg.register(_plugin("a"))
    monkeypatch.setattr(reg, "_read_list_pref", lambda key: [])
    feeds = reg.enabled_feeds()
    assert [f.name for f in feeds] == ["a_feed"]
