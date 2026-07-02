import pytest
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin
from lightfall.monitor.registry import (
    DISABLED_MONITORS_PREF, FORCED_ENABLED_MONITORS_PREF, MonitorRegistry,
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


def test_forced_enabled_overrides_disabled_by_default(monkeypatch):
    """A plugin with enabled_by_default=False that appears in
    FORCED_ENABLED_MONITORS_PREF must appear in enabled_plugins()."""
    reg = MonitorRegistry.get_instance()
    reg.register(_plugin("opt_out", enabled=False))
    reg.register(_plugin("normal"))

    def _prefs(key):
        if key == FORCED_ENABLED_MONITORS_PREF:
            return ["opt_out"]
        return []  # nothing disabled

    monkeypatch.setattr(reg, "_read_list_pref", _prefs)
    names = [p.name for p in reg.enabled_plugins()]
    assert "opt_out" in names, "force-enabled plugin must appear in enabled_plugins()"
    assert "normal" in names, "default-enabled plugin must still appear"


def test_enabled_feeds_flattens(monkeypatch):
    reg = MonitorRegistry.get_instance()
    reg.register(_plugin("a"))
    monkeypatch.setattr(reg, "_read_list_pref", lambda key: [])
    feeds = reg.enabled_feeds()
    assert [f.name for f in feeds] == ["a_feed"]
