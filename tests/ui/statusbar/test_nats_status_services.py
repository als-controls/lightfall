"""Per-service health section in the NATS status tooltip."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from lightfall.ui.statusbar.plugins.nats_status import NatsStatusPlugin


class FakeIPC(QObject):
    sigConnectionChanged = Signal(bool)
    nats_url = "tls://x:4222"
    is_connected = True

    def discover_peers(self, callback, timeout_ms=500):
        callback([])


class StubManager(QObject):
    services_changed = Signal()

    def __init__(self, statuses):
        super().__init__()
        self._statuses = statuses

    def statuses(self):
        return list(self._statuses)


def _st(name, health, version="", err=""):
    return {"name": name, "scope": "s", "health": health, "version": version, "last_error": err}


def make(qtbot, mgr):
    plugin = NatsStatusPlugin(ipc=FakeIPC(), services=mgr)
    qtbot.addWidget(plugin.create_widget())
    plugin.connect_signals()
    plugin.update()
    return plugin


def tip(plugin):
    return plugin._button.toolTip()


def test_lists_services_with_error_on_down(qtbot):
    mgr = StubManager([_st("stxm", "up", "1.2"), _st("cam", "down", "0.9", "boom")])
    t = tip(make(qtbot, mgr))
    assert "Services:" in t
    assert "stxm — up (1.2)" in t
    assert "cam — down (0.9) — boom" in t
    assert "stxm — up (1.2) —" not in t


def test_error_appended_for_unauthorized_and_incompatible_not_up(qtbot):
    mgr = StubManager(
        [_st("a", "unauthorized", "1", "denied"), _st("b", "incompatible", "1", "old"),
         _st("c", "degraded", "1", "late")]
    )
    t = tip(make(qtbot, mgr))
    assert "a — unauthorized (1) — denied" in t
    assert "b — incompatible (1) — old" in t
    assert "c — degraded (1)" in t and "late" not in t


def test_section_omitted_when_empty(qtbot):
    assert "Services" not in tip(make(qtbot, StubManager([])))


def test_updates_on_services_changed(qtbot):
    mgr = StubManager([])
    plugin = make(qtbot, mgr)
    mgr._statuses = [_st("stxm", "up", "2.0")]
    mgr.services_changed.emit()
    assert "stxm — up (2.0)" in tip(plugin)


def test_no_update_after_disconnect(qtbot):
    mgr = StubManager([])
    plugin = make(qtbot, mgr)
    plugin.disconnect_signals()
    mgr._statuses = [_st("stxm", "up", "2.0")]
    mgr.services_changed.emit()
    assert "stxm" not in tip(plugin)
