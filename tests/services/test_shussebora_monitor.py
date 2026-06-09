"""Tests for the shared shussebora monitor."""

import time

from lightfall.services.shussebora_monitor import (
    HEARTBEAT_DEAD_S,
    HEARTBEAT_STALE_S,
    ShusseboraMonitor,
)


class FakeIPC:
    is_connected = True

    def __init__(self):
        self.subscriptions = {}
        self.unsubscribed = []

    def subscribe(self, subject, callback, **kwargs):
        self.subscriptions[subject] = callback

    def unsubscribe(self, subject):
        self.unsubscribed.append(subject)

    def request(self, subject, data, timeout_ms=1000):
        return None


STATUS = {
    "hostname": "suzume",
    "version": "1.2.3",
    "uptime_s": 90000,
    "triggers": [
        {"pv_prefix": "13PICAM1:HDF1", "connected": True, "queue_depth": 2},
        {"pv_prefix": "BL7ANDOR1:HDF1", "connected": False, "queue_depth": 0},
    ],
    "disk": {"used_pct": 46.0, "free": 54},
    "transfers": {"complete_24h": 7, "failed_24h": 1, "pending": 3},
}


def make_monitor(qtbot):
    ipc = FakeIPC()
    monitor = ShusseboraMonitor(ipc=ipc)
    assert monitor.start()
    assert monitor.start()  # idempotent
    return monitor, ipc


def test_start_subscribes_once(qtbot):
    monitor, ipc = make_monitor(qtbot)
    assert set(ipc.subscriptions) == {
        "shussebora.*.heartbeat",
        "shussebora.*.transfer.complete",
        "shussebora.*.transfer.failed",
        "shussebora.*.epics.connected",
        "shussebora.*.epics.disconnected",
    }


def test_heartbeat_tracks_instance_and_emits(qtbot):
    monitor, ipc = make_monitor(qtbot)
    statuses, states = [], []
    monitor.sigStatus.connect(statuses.append)
    monitor.sigStateChanged.connect(lambda h, s: states.append((h, s)))

    ipc.subscriptions["shussebora.*.heartbeat"]("shussebora.suzume.heartbeat", STATUS, None)

    assert statuses == [STATUS]
    assert states == [("suzume", "ok")]
    assert monitor.instances()["suzume"]["state"] == "ok"
    assert monitor.worst_state() == "ok"


def test_staleness_transitions_and_worst_state(qtbot):
    monitor, ipc = make_monitor(qtbot)
    monitor.ingest_status(STATUS)
    monitor.ingest_status({**STATUS, "hostname": "otherhost"})

    monitor._instances["suzume"]["last_seen"] = time.time() - (HEARTBEAT_STALE_S + 5)
    monitor._check_staleness()
    assert monitor.instances()["suzume"]["state"] == "stale"
    assert monitor.worst_state() == "stale"

    monitor._instances["suzume"]["last_seen"] = time.time() - (HEARTBEAT_DEAD_S + 5)
    monitor._check_staleness()
    assert monitor.worst_state() == "dead"

    monitor.ingest_status(STATUS)  # heartbeat returns
    assert monitor.worst_state() == "ok"


def test_worst_state_unknown_without_instances(qtbot):
    monitor, _ = make_monitor(qtbot)
    assert monitor.worst_state() == "unknown"


def test_transfer_events_emit_kind(qtbot):
    monitor, ipc = make_monitor(qtbot)
    events = []
    monitor.sigTransfer.connect(lambda kind, data: events.append((kind, data["source"])))

    ipc.subscriptions["shussebora.*.transfer.complete"](
        "shussebora.suzume.transfer.complete", {"source": "/data/a.h5"}, None)
    ipc.subscriptions["shussebora.*.transfer.failed"](
        "shussebora.suzume.transfer.failed", {"source": "/data/b.h5", "final": True}, None)

    assert events == [("complete", "/data/a.h5"), ("failed", "/data/b.h5")]


def test_epics_event_updates_cached_triggers(qtbot):
    monitor, ipc = make_monitor(qtbot)
    monitor.ingest_status(STATUS)
    events = []
    monitor.sigEpics.connect(lambda h, c, d: events.append((h, c)))

    ipc.subscriptions["shussebora.*.epics.connected"](
        "shussebora.suzume.epics.connected", {"pv_prefix": "BL7ANDOR1:HDF1"}, None)

    assert events == [("suzume", True)]
    triggers = monitor.instances()["suzume"]["status"]["triggers"]
    assert all(t["connected"] for t in triggers)


def test_stop_unsubscribes(qtbot):
    monitor, ipc = make_monitor(qtbot)
    monitor.stop()
    assert "shussebora.*.heartbeat" in ipc.unsubscribed
    assert not monitor._started
