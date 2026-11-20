"""Tests for notify_files_closed: routing, requests, and never raising."""

import threading

import pytest

from lightfall.services import shussebora_files as sf
from lightfall.services.shussebora_monitor import ShusseboraMonitor


class FakeIPC:
    is_connected = True

    def __init__(self, replies=None):
        self.requests = []
        self.replies = replies or {}

    def subscribe(self, subject, callback, **kwargs):
        pass

    def unsubscribe(self, subject):
        pass

    def request(self, subject, data, timeout_ms=1000):
        self.requests.append((subject, data, timeout_ms))
        reply = self.replies.get(subject, "ok")
        if reply == "ok":
            return {"status": "ok", "queued": data.get("paths", []), "rejected": []}
        if isinstance(reply, Exception):
            raise reply
        return reply


def status(host, *roots, prefix=None, beamline="12.0.2.1"):
    out = {"hostname": host, "beamline": beamline, "sources": [{"root": r} for r in roots]}
    if prefix:
        out["prefix"] = prefix
    return out


@pytest.fixture
def monitor(qtbot, monkeypatch):
    ipc = FakeIPC()
    mon = ShusseboraMonitor(ipc=ipc)
    assert mon.start()
    ShusseboraMonitor._instance = mon
    sf._reported_uncovered.clear()
    # Run the worker inline so tests see its effects synchronously.
    monkeypatch.setattr(sf, "_spawn", lambda target, *args: target(*args))
    yield mon, ipc
    ShusseboraMonitor.reset_instance()


@pytest.mark.parametrize("path, root, expected", [
    ("D:/data/12.0.2.1/x.h5", "D:/data", True),
    ("d:\\DATA\\12.0.2.1\\x.h5", "D:/data", True),
    ("D:/data/../other/x.h5", "D:/data", False),
    ("D:/data/a/../b/x.h5", "D:\\data\\", True),
    ("D:/database/x.h5", "D:/data", False),
    ("\\\\nas\\share\\x.h5", "//NAS/share", True),
    ("/data/x.h5", "/data", True),
    ("/DATA/x.h5", "/data", False),
    ("/data/x.h5", "D:/data", False),
])
def test_under(path, root, expected):
    assert sf._under(path, root) is expected


def test_routes_longest_root_one_request_per_instance(monitor):
    mon, ipc = monitor
    mon.ingest_status(status("pc1", "D:/data", prefix="shussebora.12021.pc1"))
    mon.ingest_status(status("pc2", "D:/data/12.0.2.1/pi_mte"))
    sf.notify_files_closed([
        "D:\\data\\12.0.2.1\\pi_mte\\a.h5",
        "D:/data/other/b.h5",
        "d:/Data/12.0.2.1/PI_MTE/c.h5",
    ])
    assert sorted(ipc.requests) == [
        ("shussebora.12021.pc1.file.closed", {"paths": ["D:/data/other/b.h5"]}, 5000),
        ("shussebora.12021.pc2.file.closed",
         {"paths": ["D:\\data\\12.0.2.1\\pi_mte\\a.h5", "d:/Data/12.0.2.1/PI_MTE/c.h5"]}, 5000),
    ]


def test_prefix_fallback_without_beamline(monitor):
    mon, ipc = monitor
    mon.ingest_status(status("pc1", "/data", beamline=""))
    sf.notify_files_closed(["/data/x.h5"])
    assert ipc.requests[0][0] == "shussebora.pc1.file.closed"


def test_uncovered_logs_info_once_and_sends_nothing(monitor, caplog_loguru):
    mon, ipc = monitor
    mon.ingest_status(status("pc1", "D:/data"))
    sf.notify_files_closed(["C:/scratch/a.h5"])
    sf.notify_files_closed(["c:\\scratch\\b.h5"])
    assert ipc.requests == []
    infos = [r for r in caplog_loguru if r["level"] == "INFO" and "No shussebora" in r["msg"]]
    assert len(infos) == 1


def test_rejected_and_missing_reply_logged_as_warning(monitor, caplog_loguru):
    mon, ipc = monitor
    mon.ingest_status(status("pc1", "D:/data"))
    mon.ingest_status(status("pc2", "E:/data"))
    ipc.replies["shussebora.12021.pc1.file.closed"] = {
        "status": "ok", "queued": [], "rejected": [{"path": "D:/data/a.h5", "reason": "missing"}]}
    ipc.replies["shussebora.12021.pc2.file.closed"] = None
    sf.notify_files_closed(["D:/data/a.h5", "E:/data/b.h5"])
    warnings = [r["msg"] for r in caplog_loguru if r["level"] == "WARNING"]
    assert any("rejected D:/data/a.h5: missing" in w for w in warnings)
    assert any("no reply" in w for w in warnings)


def test_request_exception_never_raises(monitor, caplog_loguru):
    mon, ipc = monitor
    mon.ingest_status(status("pc1", "D:/data"))
    ipc.replies["shussebora.12021.pc1.file.closed"] = RuntimeError("boom")
    sf.notify_files_closed(["D:/data/a.h5"])
    assert any("boom" in r["msg"] for r in caplog_loguru if r["level"] == "WARNING")


def test_broken_monitor_never_raises(monitor, monkeypatch):
    mon, _ = monitor
    monkeypatch.setattr(mon, "instances", lambda: 1 / 0)
    sf.notify_files_closed(["D:/data/a.h5"])  # no exception


def test_starts_monitor_when_missing(qtbot, monkeypatch):
    ShusseboraMonitor.reset_instance()
    ipc = FakeIPC()
    monkeypatch.setattr(ShusseboraMonitor, "_ipc", lambda self: ipc)
    monkeypatch.setattr(sf, "_spawn", lambda target, *args: target(*args))
    try:
        sf.notify_files_closed(["D:/data/a.h5"])
        assert ShusseboraMonitor._instance is not None
        assert ShusseboraMonitor._instance.started
    finally:
        ShusseboraMonitor._instance.stop()
        ShusseboraMonitor.reset_instance()


def test_does_not_block_caller(qtbot):
    gate = threading.Event()
    done = threading.Event()

    class SlowIPC(FakeIPC):
        def request(self, subject, data, timeout_ms=1000):
            gate.wait(5)
            done.set()
            return {"status": "ok", "queued": data["paths"], "rejected": []}

    mon = ShusseboraMonitor(ipc=SlowIPC())
    mon.start()
    ShusseboraMonitor._instance = mon
    try:
        mon.ingest_status(status("pc1", "D:/data"))
        sf.notify_files_closed(["D:/data/a.h5"])  # returns while the request is pending
        assert not done.is_set()
        gate.set()
        assert done.wait(5)
    finally:
        mon.stop()
        ShusseboraMonitor.reset_instance()


@pytest.fixture
def caplog_loguru():
    from lightfall.utils.logging import logger

    records = []
    handler_id = logger.add(
        lambda m: records.append({"level": m.record["level"].name, "msg": m.record["message"]}),
        level="DEBUG")
    yield records
    logger.remove(handler_id)
