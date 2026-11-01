"""Monitors page characterization (old page), then the scope-aware catalog page."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin
from lightfall.monitor.registry import MonitorRegistry


def make_feed(name, interval=30.0):
    return type(f"Feed_{name}", (MonitorFeed,), {
        "name": name,
        "default_interval_s": interval,
        "evaluate": lambda self, ctx, window, prior: None,
    })()


def make_monitor(name, feeds=(), category="general", default=True):
    return type(f"Monitor_{name}", (MonitorPlugin,), {
        "name": property(lambda self: name),
        "description": property(lambda self: f"{name} monitor"),
        "category": property(lambda self: category),
        "enabled_by_default": property(lambda self: default),
        "create_feeds": lambda self: [make_feed(*f) for f in feeds],
    })()


def monitors():
    return [
        make_monitor("health", [("acq_health", 15.0), ("beam_status", 60.0)]),
        make_monitor("xpcs", [("xpcs_quality", 30.0)], "analysis", default=False),
    ]


@pytest.fixture
def registry():
    MonitorRegistry.reset_instance()
    reg = MonitorRegistry.get_instance()
    for m in monitors():
        reg.register(m)
    yield reg
    MonitorRegistry.reset_instance()


class OldPrefs:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.sets: list[tuple] = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, **kw):
        self.sets.append((key, value))
        self.values[key] = value

    def remove(self, key, **kw):
        self.values.pop(key, None)


def test_old_page_characterization(qtbot, monkeypatch, registry):
    """What the pre-catalog page showed and wrote.

    It wrote seven keys: the two scalars, two plugin lists (``disabled_monitor_plugins``,
    ``forced_enabled_monitor_plugins``), a feed list (``disabled_monitor_feeds``) and two
    per-feed dicts (``monitor_feed_intervals``, ``monitor_feed_advisor_severity``). Feeds of a
    disabled monitor plugin were drawn grey. A typed default interval or "info" severity was
    not stored. The catalog page writes ``monitors.enabled`` {"health": False, "xpcs": True},
    ``monitor_feeds.enabled`` {"beam_status": False}, ``monitor_feeds.interval``
    {"acq_health": 5} and ``monitor_feeds.severity`` {"acq_health": "warn"}.
    """
    from lightfall.ui.preferences.monitor_settings import MonitorSettingsPlugin

    prefs = OldPrefs({"disabled_monitor_plugins": [], "forced_enabled_monitor_plugins": [],
                      "monitor_advisor_enabled": False, "monitor_tick_interval": 60})
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    page = MonitorSettingsPlugin()
    assert (page.name, page.display_name, page.category, page.priority) == ("monitor", "Monitor", "advanced", 95)
    page.create_widget()
    page.load_settings()
    assert page._advisor_check.isChecked() is False
    assert page._interval_spin.value() == 60
    assert (page._interval_spin.minimum(), page._interval_spin.maximum()) == (1, 86400)

    model = page._model
    rows = [
        (model.data(model.index(r, 0)), model.data(model.index(r, 0), Qt.ItemDataRole.CheckStateRole),
         model.data(model.index(r, 1)), model.data(model.index(r, 2)))
        for r in range(model.rowCount())
    ]
    assert rows == [  # sorted by category, then display name
        ("Xpcs", Qt.CheckState.Unchecked, "Analysis", "xpcs monitor"),
        ("Health", Qt.CheckState.Checked, "General", "health monitor"),
    ]

    feeds = page._feed_model
    assert feeds.COLUMNS == ["Feed", "Plugin", "Interval (s)", "Advisor min severity"]
    feed_rows = [
        tuple(feeds.data(feeds.index(r, c)) for c in range(4))
        for r in range(feeds.rowCount())
    ]
    assert feed_rows == [  # registration order, every plugin's feeds
        ("acq_health", "health", "", "info"),
        ("beam_status", "health", "", "info"),
        ("xpcs_quality", "xpcs", "", "info"),
    ]
    grey = {feeds.data(feeds.index(r, 0)): feeds.data(feeds.index(r, 0), Qt.ItemDataRole.ForegroundRole)
            for r in range(feeds.rowCount())}
    assert grey["acq_health"] is None
    assert grey["xpcs_quality"] is not None  # its plugin (off by default) is not enabled
    assert feeds.data(feeds.index(0, 2), Qt.ItemDataRole.ToolTipRole) == "Default: 15.0s"

    model.setData(model.index(1, 0), Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
    model.setData(model.index(0, 0), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
    feeds.setData(feeds.index(1, 0), Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
    assert feeds.setData(feeds.index(0, 2), "0", Qt.ItemDataRole.EditRole) is False  # > 0 only
    assert feeds.setData(feeds.index(0, 2), "5", Qt.ItemDataRole.EditRole) is True
    assert feeds.setData(feeds.index(2, 2), "30", Qt.ItemDataRole.EditRole) is True  # its default
    assert feeds.setData(feeds.index(0, 3), "loud", Qt.ItemDataRole.EditRole) is False
    assert feeds.setData(feeds.index(0, 3), "warn", Qt.ItemDataRole.EditRole) is True
    assert feeds.setData(feeds.index(1, 3), "info", Qt.ItemDataRole.EditRole) is True  # the default
    page._advisor_check.setChecked(True)
    page._interval_spin.setValue(45)
    page.save_settings()
    assert prefs.sets == [
        ("disabled_monitor_plugins", ["health"]),
        ("forced_enabled_monitor_plugins", ["xpcs"]),
        ("monitor_advisor_enabled", True),
        ("monitor_tick_interval", 45),
        ("disabled_monitor_feeds", ["beam_status"]),
        ("monitor_feed_intervals", {"acq_health": 5}),
        ("monitor_feed_advisor_severity", {"acq_health": "warn"}),
    ]
