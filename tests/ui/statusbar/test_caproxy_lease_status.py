"""Tests for the caproxy lease status bar indicator."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from lightfall.ui.statusbar.plugins.caproxy_lease_status import (
    CaproxyLeaseStatusPlugin,
    _format_countdown,
)


class FakeLeaseService(QObject):
    leases_updated = Signal(list)
    poll_error = Signal(str)

    def __init__(self):
        super().__init__()
        self.start_calls = 0
        self.stop_calls = 0

    def start_polling(self):
        self.start_calls += 1

    def stop_polling(self):
        self.stop_calls += 1


def make_plugin(qtbot, service, monkeypatch, now=1000.0):
    monkeypatch.setattr(
        "lightfall.ui.statusbar.plugins.caproxy_lease_status.time.time", lambda: now
    )
    plugin = CaproxyLeaseStatusPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    monkeypatch.setattr(
        "lightfall.services.caproxy_lease_service.CaproxyLeaseService.get_instance",
        lambda: service,
    )
    plugin.connect_signals()
    plugin.update()
    return plugin


def test_format_countdown():
    assert _format_countdown(125) == "02:05"
    assert _format_countdown(0) == "00:00"
    assert _format_countdown(-5) == "00:00"


def test_idle_state_no_leases(qtbot, monkeypatch):
    service = FakeLeaseService()
    plugin = make_plugin(qtbot, service, monkeypatch)
    service.leases_updated.emit([])
    assert plugin._button.text() == ""
    assert "No active leases" in plugin._button.toolTip()


def test_pending_state(qtbot, monkeypatch):
    service = FakeLeaseService()
    plugin = make_plugin(qtbot, service, monkeypatch)
    service.leases_updated.emit(
        [{"state": "pending", "pv_patterns": ["es:motor:z*"]}]
    )
    assert plugin._button.text() == "lease pending"
    assert "es:motor:z*" in plugin._button.toolTip()


def test_active_state_shows_countdown(qtbot, monkeypatch):
    service = FakeLeaseService()
    plugin = make_plugin(qtbot, service, monkeypatch, now=1000.0)
    service.leases_updated.emit(
        [{"state": "active", "pv_patterns": ["es:motor:z*"], "expires_at": 1090.0}]
    )
    assert plugin._button.text() == "01:30"
    assert "es:motor:z*" in plugin._button.toolTip()


def test_active_picks_soonest_expiry(qtbot, monkeypatch):
    service = FakeLeaseService()
    plugin = make_plugin(qtbot, service, monkeypatch, now=1000.0)
    service.leases_updated.emit(
        [
            {"state": "active", "pv_patterns": ["a"], "expires_at": 1200.0},
            {"state": "active", "pv_patterns": ["b"], "expires_at": 1060.0},
        ]
    )
    assert plugin._button.text() == "01:00"


def test_countdown_ticks_down(qtbot, monkeypatch):
    service = FakeLeaseService()
    now = {"t": 1000.0}
    monkeypatch.setattr(
        "lightfall.ui.statusbar.plugins.caproxy_lease_status.time.time",
        lambda: now["t"],
    )
    plugin = CaproxyLeaseStatusPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    monkeypatch.setattr(
        "lightfall.services.caproxy_lease_service.CaproxyLeaseService.get_instance",
        lambda: service,
    )
    plugin.connect_signals()
    service.leases_updated.emit(
        [{"state": "active", "pv_patterns": ["es:motor:z*"], "expires_at": 1090.0}]
    )
    assert plugin._button.text() == "01:30"
    now["t"] = 1010.0
    plugin._on_tick()
    assert plugin._button.text() == "01:20"
    plugin.disconnect_signals()


def test_poll_error_state(qtbot, monkeypatch):
    service = FakeLeaseService()
    plugin = make_plugin(qtbot, service, monkeypatch)
    service.poll_error.emit("connection refused")
    assert plugin._button.text() == "lease ?"
    assert "connection refused" in plugin._button.toolTip()


def test_poll_error_takes_precedence_over_stale_leases(qtbot, monkeypatch):
    service = FakeLeaseService()
    plugin = make_plugin(qtbot, service, monkeypatch)
    service.leases_updated.emit(
        [{"state": "active", "pv_patterns": ["a"], "expires_at": 1090.0}]
    )
    service.poll_error.emit("timeout")
    assert plugin._button.text() == "lease ?"


def test_connect_signals_starts_polling(qtbot, monkeypatch):
    service = FakeLeaseService()
    plugin = make_plugin(qtbot, service, monkeypatch)
    assert service.start_calls == 1


def test_disconnect_signals_stops_polling_and_timer(qtbot, monkeypatch):
    service = FakeLeaseService()
    plugin = make_plugin(qtbot, service, monkeypatch)
    assert plugin._timer.isActive()
    plugin.disconnect_signals()
    assert service.stop_calls == 1
    assert not plugin._timer.isActive()


def test_registered_in_builtin_manifest():
    from lightfall.plugins.builtin_manifest import builtin_manifest

    entry = next(
        (
            e
            for e in builtin_manifest.plugins
            if e.type_name == "statusbar" and e.name == "caproxy_lease_status"
        ),
        None,
    )
    assert entry is not None
    assert entry.import_path == (
        "lightfall.ui.statusbar.plugins.caproxy_lease_status:CaproxyLeaseStatusPlugin"
    )


def test_exported_from_plugins_package():
    from lightfall.ui.statusbar.plugins import CaproxyLeaseStatusPlugin as Exported

    assert Exported is CaproxyLeaseStatusPlugin


def test_tick_does_not_update_without_active_lease(qtbot, monkeypatch):
    """Verify that _on_tick skips update() when there's no active lease."""
    service = FakeLeaseService()
    plugin = make_plugin(qtbot, service, monkeypatch)

    # Emit idle state (no leases)
    service.leases_updated.emit([])

    # Mock update to verify it's not called
    update_calls = []

    def mock_update():
        update_calls.append(True)

    plugin.update = mock_update

    # Tick should not call update when idle
    plugin._on_tick()
    assert not update_calls

    # Now emit pending lease
    service.leases_updated.emit([{"state": "pending", "pv_patterns": ["es:motor:z*"]}])
    update_calls.clear()

    # Tick should still not call update when pending (not active)
    plugin._on_tick()
    assert not update_calls

    # Now emit active lease and verify tick DOES call update
    service.leases_updated.emit(
        [{"state": "active", "pv_patterns": ["es:motor:z*"], "expires_at": 1090.0}]
    )
    update_calls.clear()

    plugin._on_tick()
    assert update_calls == [True]
