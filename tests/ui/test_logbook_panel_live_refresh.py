from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def panel_class():
    from lightfall.ui.panels.logbook_panel import LogbookPanel
    return LogbookPanel


def _panel(panel_class):
    p = panel_class.__new__(panel_class)
    p._client = MagicMock()
    p._logbook_id = "lb"
    p._current_entry_id = "e1"
    p._entries = {}
    p._load_entries = MagicMock()
    p._select_entry = MagicMock()
    p._entry_widget_has_focus = MagicMock(return_value=False)
    return p


def test_on_pull_reloads_list(qapp, panel_class):
    p = _panel(panel_class)
    p._client.get_entry.return_value = {"id": "e1", "updated_at": "t2"}
    p._displayed_updated_at = "t1"
    p._on_pull()
    p._load_entries.assert_called_once()


def test_on_pull_rerenders_open_entry_when_changed_and_unfocused(qapp, panel_class):
    p = _panel(panel_class)
    p._displayed_updated_at = "t1"
    p._client.get_entry.return_value = {"id": "e1", "updated_at": "t2"}
    p._on_pull()
    p._select_entry.assert_called_once_with("e1")


def test_on_pull_skips_rerender_when_focused(qapp, panel_class):
    p = _panel(panel_class)
    p._entry_widget_has_focus.return_value = True
    p._displayed_updated_at = "t1"
    p._client.get_entry.return_value = {"id": "e1", "updated_at": "t2"}
    p._on_pull()
    p._select_entry.assert_not_called()


def test_on_pull_skips_rerender_when_unchanged(qapp, panel_class):
    p = _panel(panel_class)
    p._displayed_updated_at = "t1"
    p._client.get_entry.return_value = {"id": "e1", "updated_at": "t1"}
    p._on_pull()
    p._select_entry.assert_not_called()


def test_pull_callback_registered_and_live_started(qapp, panel_class, monkeypatch):
    import lightfall.ui.panels.logbook_panel as mod

    class _FakeLive:
        def __init__(self, client):
            self.started_with = None
        def start(self, server_url):
            self.started_with = server_url
        def on_user_changed(self, server_url):
            pass

    monkeypatch.setattr(mod, "LogbookLiveUpdates", _FakeLive)

    p = panel_class.__new__(panel_class)
    p._client = MagicMock()
    p._client._server_url = "http://lb.test"
    p._on_pull = MagicMock()

    p._start_live_updates()

    p._client.set_on_pull_callback.assert_called_once_with(p._on_pull)
    assert isinstance(p._live, _FakeLive)
    assert p._live.started_with == "http://lb.test"


def test_on_closing_stops_live_updates(qapp, panel_class):
    p = panel_class.__new__(panel_class)
    p._live = MagicMock()
    p._on_closing()
    p._live.stop.assert_called_once()


def test_on_closing_safe_without_live(qapp, panel_class):
    p = panel_class.__new__(panel_class)
    # _live never set (e.g. init failed before _start_live_updates) — must not raise.
    p._on_closing()
