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
    assert plugin._button.text() == "⬆ 2 updates"
    assert "lightfall" in plugin._button.toolTip()
    assert "lightfall-saxs" in plugin._button.toolTip()
    checker.publish(CheckResult.EMPTY)
    assert not plugin.is_visible


def test_singular_label(qtbot):
    checker = FakeChecker(CheckResult(repos=(repo("lightfall", behind=1),)))
    assert make(qtbot, checker)._button.text() == "⬆ 1 update"


def test_restart_pending_shows(qtbot):
    checker = FakeChecker()
    plugin = make(qtbot, checker)
    checker.mark_restart_pending()
    assert plugin.is_visible
    assert plugin._button.text() == "⟳ Restart pending"
    plugin.disconnect_signals()
