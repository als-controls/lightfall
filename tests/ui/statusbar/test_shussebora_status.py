"""Tests for the shussebora status bar indicator."""

import time

from lightfall.services.shussebora_monitor import HEARTBEAT_DEAD_S, ShusseboraMonitor
from lightfall.ui.statusbar.plugins.shussebora_status import ShusseboraStatusPlugin


class FakeIPC:
    is_connected = True

    def __init__(self):
        self.subscriptions = {}

    def subscribe(self, subject, callback, **kwargs):
        self.subscriptions[subject] = callback

    def unsubscribe(self, subject):
        pass

    def request(self, subject, data, timeout_ms=1000):
        return None


STATUS = {
    "hostname": "suzume",
    "uptime_s": 1000,
    "triggers": [{"pv_prefix": "13PICAM1:HDF1", "connected": True, "queue_depth": 3}],
    "disk": {"used_pct": 46.0, "free": 54},
    "transfers": {"complete_24h": 7, "failed_24h": 1, "pending": 3},
}


def make_plugin(qtbot):
    monitor = ShusseboraMonitor(ipc=FakeIPC())
    plugin = ShusseboraStatusPlugin(monitor=monitor)
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    plugin.connect_signals()
    plugin.update()
    return plugin, monitor


def test_unknown_before_any_heartbeat(qtbot):
    plugin, monitor = make_plugin(qtbot)
    assert plugin._button.text() == "—"
    assert "no instances" in plugin._button.toolTip()


def test_heartbeat_shows_ok_with_queue(qtbot):
    plugin, monitor = make_plugin(qtbot)
    monitor.ingest_status(STATUS)
    assert plugin._button.text() == "OK · 3 queued"
    assert "suzume: OK" in plugin._button.toolTip()
    assert "7 ok / 1 failed" in plugin._button.toolTip()


def test_dead_instance_shows_down(qtbot):
    plugin, monitor = make_plugin(qtbot)
    monitor.ingest_status(STATUS)
    monitor._instances["suzume"]["last_seen"] = time.time() - (HEARTBEAT_DEAD_S + 5)
    monitor._check_staleness()
    assert plugin._button.text() == "Down"


def test_introspection_reports_states(qtbot):
    plugin, monitor = make_plugin(qtbot)
    monitor.ingest_status(STATUS)
    data = plugin.get_introspection_data()
    assert data["worst_state"] == "ok"
    assert data["instances"] == {"suzume": "ok"}
