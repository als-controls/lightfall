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
