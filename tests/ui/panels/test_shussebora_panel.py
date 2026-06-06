"""Tests for the Shussebora data-movement status panel."""

import time

from lightfall.ui.panels.shussebora_panel import (
    HEARTBEAT_DEAD_S,
    HEARTBEAT_STALE_S,
    ShusseboraPanel,
)


class FakeIPC:
    """Minimal IPCService stand-in recording subscriptions."""

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
        {"pv_prefix": "13PICAM1:HDF1", "connected": True, "last_file": "/data/x.h5", "queue_depth": 2},
        {"pv_prefix": "BL7ANDOR1:HDF1", "connected": False, "last_file": None, "queue_depth": 0},
    ],
    "disk": {"path": "/data", "total": 100, "used": 46, "free": 54, "used_pct": 46.0},
    "transfers": {"complete_24h": 7, "failed_24h": 1, "pending": 3},
}


def make_panel(qtbot, ipc=None):
    panel = ShusseboraPanel(ipc=ipc or FakeIPC())
    qtbot.addWidget(panel)
    return panel


def test_subscribes_to_wildcard_subjects(qtbot):
    ipc = FakeIPC()
    make_panel(qtbot, ipc)
    assert "shussebora.*.heartbeat" in ipc.subscriptions
    assert "shussebora.*.transfer.complete" in ipc.subscriptions
    assert "shussebora.*.transfer.failed" in ipc.subscriptions


def test_heartbeat_creates_instance_card(qtbot):
    ipc = FakeIPC()
    panel = make_panel(qtbot, ipc)
    assert panel.instance_count() == 0

    ipc.subscriptions["shussebora.*.heartbeat"]("shussebora.suzume.heartbeat", STATUS, None)

    assert panel.instance_count() == 1
    assert panel.instance_state("suzume") == "ok"
    card = panel._instances["suzume"]
    assert "46.0%" in card["detail"].text()
    assert "queue: 2" in card["detail"].text()
    assert "7 ok, 1 failed" in card["detail"].text()
    assert "13PICAM1:HDF1" in card["triggers"].text()


def test_staleness_transitions(qtbot):
    ipc = FakeIPC()
    panel = make_panel(qtbot, ipc)
    panel._apply_status(STATUS)
    card = panel._instances["suzume"]

    card["last_seen"] = time.time() - (HEARTBEAT_STALE_S + 5)
    panel._check_staleness()
    assert panel.instance_state("suzume") == "stale"

    card["last_seen"] = time.time() - (HEARTBEAT_DEAD_S + 5)
    panel._check_staleness()
    assert panel.instance_state("suzume") == "dead"

    panel._apply_status(STATUS)  # heartbeat returns
    assert panel.instance_state("suzume") == "ok"


def test_transfer_events_fill_table(qtbot):
    ipc = FakeIPC()
    panel = make_panel(qtbot, ipc)

    ipc.subscriptions["shussebora.*.transfer.complete"](
        "shussebora.suzume.transfer.complete",
        {"source": "/data/a.h5", "trigger": "13PICAM1:HDF1", "bytes": 2048, "duration": 1.5},
        None)
    ipc.subscriptions["shussebora.*.transfer.failed"](
        "shussebora.suzume.transfer.failed",
        {"source": "/data/b.h5", "trigger": "13PICAM1:HDF1", "error": "x", "attempts": 5,
         "final": True},
        None)
    ipc.subscriptions["shussebora.*.transfer.failed"](
        "shussebora.suzume.transfer.failed",
        {"source": "/data/c.h5", "trigger": "13PICAM1:HDF1", "error": "x", "attempts": 1,
         "final": False},
        None)

    assert panel.transfer_row_count() == 3
    # newest first
    assert panel.transfer_row(0)["status"] == "retry 1"
    assert panel.transfer_row(1)["status"] == "FAILED"
    assert panel.transfer_row(2)["status"] == "complete"
    assert panel.transfer_row(2)["source"] == "/data/a.h5"


def test_epics_event_updates_trigger_marks(qtbot):
    ipc = FakeIPC()
    panel = make_panel(qtbot, ipc)
    panel._apply_status(STATUS)
    card = panel._instances["suzume"]
    assert "✗" in card["triggers"].text()  # ANDOR disconnected in STATUS

    ipc.subscriptions["shussebora.*.epics.connected"](
        "shussebora.suzume.epics.connected", {"pv_prefix": "BL7ANDOR1:HDF1"}, None)
    assert "✗" not in card["triggers"].text()


def test_recent_transfers_replace_table(qtbot):
    ipc = FakeIPC()
    panel = make_panel(qtbot, ipc)
    panel._apply_recent([
        {"source": "/data/a.h5", "trigger": "catchup", "status": "complete",
         "bytes": 100, "finished": "2026-06-05 14:22:26.707738"},
        {"source": "/data/b.h5", "trigger": "13PICAM1:HDF1", "status": "failed",
         "bytes": None, "created": "2026-06-05 13:00:00"},
    ])
    assert panel.transfer_row_count() == 2
    assert panel.transfer_row(0)["time"] == "14:22:26"
    assert panel.transfer_row(1)["status"] == "failed"


def test_closing_unsubscribes(qtbot):
    ipc = FakeIPC()
    panel = make_panel(qtbot, ipc)
    panel._on_closing()
    assert "shussebora.*.heartbeat" in ipc.unsubscribed
