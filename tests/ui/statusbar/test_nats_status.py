"""Tests for the NATS connection status bar indicator."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from lightfall.ui.statusbar.plugins.nats_status import NatsStatusPlugin


class FakeIPC(QObject):
    sigConnectionChanged = Signal(bool)

    def __init__(self, connected=True, peers=None):
        super().__init__()
        self._connected = connected
        self._peers = peers or []
        self.nats_url = "tls://bcgnats:4222"
        self.instance_id = "host-self"
        self.discover_calls = 0

    @property
    def is_connected(self):
        return self._connected

    def discover_peers(self, callback, timeout_ms=500):
        self.discover_calls += 1
        callback(list(self._peers))


PEERS = [
    {"instance_id": "host-self", "display_name": "Lightfall", "prefix": "als", "is_self": True},
    {"instance_id": "host-2", "display_name": "Tsuchinoko", "prefix": "tsk", "is_self": False},
]


def make_plugin(qtbot, ipc):
    plugin = NatsStatusPlugin(ipc=ipc)
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    plugin.connect_signals()
    plugin.update()
    return plugin


def test_connected_shows_message_text_and_peer_count(qtbot):
    plugin = make_plugin(qtbot, FakeIPC(connected=True, peers=PEERS))
    assert plugin._icon_name == "mdi6.message-text"
    assert plugin._button.text() == "1"
    assert "Connected" in plugin._button.toolTip()
    assert "Tsuchinoko" in plugin._button.toolTip()
    assert "this instance" in plugin._button.toolTip()


def test_connected_no_peers_shows_blank_text(qtbot):
    plugin = make_plugin(qtbot, FakeIPC(connected=True, peers=[]))
    assert plugin._icon_name == "mdi6.message-text"
    assert plugin._button.text() == ""
    assert "click to refresh" in plugin._button.toolTip()


def test_disconnected_shows_message_off(qtbot):
    plugin = make_plugin(qtbot, FakeIPC(connected=False))
    assert plugin._icon_name == "mdi6.message-off-outline"
    assert "not connected" in plugin._button.toolTip()


def test_disconnect_signal_clears_peers(qtbot):
    ipc = FakeIPC(connected=True, peers=PEERS)
    plugin = make_plugin(qtbot, ipc)
    assert plugin._peers  # populated on connect
    ipc._connected = False
    ipc.sigConnectionChanged.emit(False)
    assert plugin._peers == []
    assert plugin._icon_name == "mdi6.message-off-outline"
    assert "not connected" in plugin._button.toolTip()


def test_not_configured_shows_message_outline(qtbot, monkeypatch):
    monkeypatch.setattr(
        "lightfall.ui.statusbar.plugins.nats_status.get_ipc_service", lambda: None
    )
    plugin = make_plugin(qtbot, None)
    assert plugin._icon_name == "mdi6.message-outline"
    assert plugin._button.text() == "Off"


def test_click_triggers_refresh_and_timestamp(qtbot):
    ipc = FakeIPC(connected=True, peers=PEERS)
    plugin = make_plugin(qtbot, ipc)
    calls_before = ipc.discover_calls
    plugin.on_clicked()
    assert ipc.discover_calls == calls_before + 1
    assert plugin._last_refreshed is not None
    assert "Last refreshed" in plugin._button.toolTip()


def test_introspection_reports_connection_and_peers(qtbot):
    plugin = make_plugin(qtbot, FakeIPC(connected=True, peers=PEERS))
    data = plugin.get_introspection_data()
    assert data["connected"] is True
    assert data["peer_count"] == 1
    assert len(data["peers"]) == 2
