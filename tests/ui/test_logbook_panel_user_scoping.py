"""LogbookPanel must scope the displayed logbook to the authenticated user.

Bug 1: the panel resolved its logbook via ``getpass.getuser()`` (the OS
account), which is identical for every Lightfall user on a shared install.
Switching the authenticated user therefore left the previous user's entries
on screen. The panel must (a) key the logbook by the session username and
(b) re-resolve + reload when the session user changes.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def panel_class():
    """Import lazily so the qapp fixture has fired first."""
    from lightfall.ui.panels.logbook_panel import LogbookPanel
    return LogbookPanel


def _make_panel(panel_class, fake_client):
    """Construct a LogbookPanel without running the heavy __init__/_setup_ui.

    Mirrors the repo's __new__-based pattern (see test_auth_v2_logout_re_gate).
    """
    panel = panel_class.__new__(panel_class)
    panel._client = fake_client
    panel._logbook_id = "old-logbook"
    panel._current_entry_id = "stale-entry"
    panel._entries = {"stale-entry": MagicMock()}
    # Avoid touching real Qt widgets / banner state.
    panel._update_guest_banner = MagicMock()
    panel._load_entries = MagicMock()
    return panel


def test_user_change_rescopes_logbook_to_new_user(qapp, panel_class):
    fake_client = MagicMock()
    fake_client.get_or_create_logbook.return_value = "logbook-for-bob"
    panel = _make_panel(panel_class, fake_client)

    from lightfall.auth.session import User
    bob = User(username="bob")

    panel._on_user_changed(bob)

    fake_client.get_or_create_logbook.assert_called_once_with("bob")
    assert panel._logbook_id == "logbook-for-bob"
    # Stale in-memory entries from the previous user are cleared and reloaded.
    assert panel._entries == {}
    panel._load_entries.assert_called_once()


def test_user_change_does_not_use_os_login(qapp, panel_class, monkeypatch):
    """The logbook key must come from the User, never getpass/OS identity."""
    import getpass

    monkeypatch.setattr(getpass, "getuser", lambda: "os-account")
    fake_client = MagicMock()
    fake_client.get_or_create_logbook.return_value = "logbook-for-carol"
    panel = _make_panel(panel_class, fake_client)

    from lightfall.auth.session import User
    panel._on_user_changed(User(username="carol"))

    fake_client.get_or_create_logbook.assert_called_once_with("carol")
    assert "os-account" not in [
        c.args[0] for c in fake_client.get_or_create_logbook.call_args_list
    ]
