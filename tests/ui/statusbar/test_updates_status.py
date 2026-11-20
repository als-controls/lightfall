"""Tests for the updates status bar indicator."""

from __future__ import annotations

from lightfall.ui.statusbar.plugins.updates_status import UpdatesStatusPlugin
from lightfall.updates.model import CheckResult
from lightfall.updates.pypi import PypiState
from tests.updates.fakes import FakeChecker, repo

TWO = CheckResult(repos=(repo("lightfall", behind=3), repo("lightfall-deck")),
                  pypi=(PypiState("lightfall-saxs", "1.0", "1.1"),))


def make(qtbot, checker):
    plugin = UpdatesStatusPlugin(checker=checker)
    qtbot.addWidget(plugin.create_widget())
    plugin.connect_signals()
    return plugin


def test_hidden_with_no_updates(qtbot):
    checker = FakeChecker()
    plugin = make(qtbot, checker)
    assert not plugin.is_visible
    assert checker.gate_refreshes == 1


def test_shown_with_count(qtbot):
    checker = FakeChecker()
    plugin = make(qtbot, checker)
    checker.publish(TWO)
    assert plugin.is_visible
    assert plugin._button.text() == "2 updates"
    assert "lightfall" in plugin._button.toolTip()
    assert "lightfall-saxs" in plugin._button.toolTip()
    checker.publish(CheckResult.EMPTY)
    assert not plugin.is_visible


def test_singular_label(qtbot):
    checker = FakeChecker(CheckResult(repos=(repo("lightfall", behind=1),)))
    assert make(qtbot, checker)._button.text() == "1 update"


def test_restart_pending_shows(qtbot):
    checker = FakeChecker()
    plugin = make(qtbot, checker)
    checker.mark_restart_pending()
    assert plugin.is_visible
    assert plugin._button.text() == "Restart pending"
    plugin.disconnect_signals()


def test_restart_pending_hidden_after_logout(qtbot, monkeypatch):
    from lightfall.utils import restart

    checker = FakeChecker()
    plugin = make(qtbot, checker)
    checker.mark_restart_pending()
    checker.allowed = False
    checker.deactivate()
    assert not plugin.is_visible
    calls = []
    monkeypatch.setattr(restart, "request_restart", lambda: calls.append(1))
    plugin.on_clicked()
    assert calls == []


def themed(qtbot, checker):
    from tests.updates.fakes import FakeTheme

    plugin = UpdatesStatusPlugin(checker=checker)
    plugin._theme_manager = FakeTheme()
    qtbot.addWidget(plugin.create_widget())
    plugin.connect_signals()
    return plugin


def test_uses_themed_icon_not_glyphs(qtbot):
    checker = FakeChecker()
    plugin = themed(qtbot, checker)
    checker.publish(TWO)
    assert not plugin._button.icon().isNull()
    assert plugin._button.text().isascii()
    assert "#1e88e5" in plugin._button.styleSheet()
    plugin._theme_manager.switch(info="#90caf9", warning="#ffcc80")
    assert "#90caf9" in plugin._button.styleSheet()


def test_restart_pending_uses_warning_color(qtbot):
    checker = FakeChecker()
    plugin = themed(qtbot, checker)
    checker.mark_restart_pending()
    assert not plugin._button.icon().isNull()
    assert "#fb8c00" in plugin._button.styleSheet()


def test_detach_theme_stops_rerendering(qtbot):
    checker = FakeChecker()
    plugin = themed(qtbot, checker)
    checker.publish(TWO)
    plugin.disconnect_signals()
    plugin._detach_theme()  # the StatusBarManager does this on removal
    plugin._theme_manager.switch(info="#90caf9", warning="#ffcc80")
    assert "#1e88e5" in plugin._button.styleSheet()
