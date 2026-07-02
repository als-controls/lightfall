from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin


class _Feed(MonitorFeed):
    name = "f1"
    def evaluate(self, ctx, window, prior):
        return None


class _Plugin(MonitorPlugin):
    @property
    def name(self): return "demo"
    @property
    def description(self): return "demo monitor"
    def create_feeds(self): return [_Feed()]


def test_monitor_plugin_defaults_and_feeds():
    p = _Plugin()
    assert p.type_name == "monitor"
    assert p.is_singleton is True
    assert p.enabled_by_default is True
    assert p.priority == 100
    assert p.display_name == "Demo"
    feeds = p.create_feeds()
    assert len(feeds) == 1 and feeds[0].name == "f1"
    intro = p.get_introspection_data()
    assert intro["type"] == "monitor" and intro["feed_count"] == 1
