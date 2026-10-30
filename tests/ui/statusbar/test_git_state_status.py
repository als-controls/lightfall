"""Tests for the git-state status bar indicator and its popup."""

from __future__ import annotations

from lightfall.ui.dialogs.git_state_dialog import GitStateDialog
from lightfall.ui.statusbar.plugins.git_state_status import GitStateStatusPlugin
from lightfall.updates.model import CheckResult
from tests.updates.fakes import FakeChecker, repo


def make(qtbot, checker):
    plugin = GitStateStatusPlugin(checker=checker)
    qtbot.addWidget(plugin.create_widget())
    plugin.connect_signals()
    return plugin


def test_hidden_when_all_clean(qtbot):
    checker = FakeChecker(CheckResult(repos=(repo("lightfall"), repo("lightfall-deck"))))
    assert not make(qtbot, checker).is_visible


def test_shows_feature_branch_and_dirty(qtbot):
    checker = FakeChecker()
    plugin = make(qtbot, checker)
    checker.publish(CheckResult(repos=(
        repo("lightfall-deck", branch="feat/x", dirty=True),
        repo("lightfall-voice", ahead=2),
        repo("lightfall"),
    )))
    assert plugin.is_visible
    assert plugin._button.text() == "⎇ 2 repos"
    tip = plugin._button.toolTip()
    assert "lightfall-deck: feat/x, uncommitted changes" in tip
    assert "lightfall-voice: master, ahead 2" in tip
    assert "lightfall:" not in tip


def test_detached_head_label(qtbot):
    checker = FakeChecker(CheckResult(repos=(repo("lightfall", branch=None),)))
    plugin = make(qtbot, checker)
    assert plugin._button.text() == "⎇ 1 repo"
    assert "lightfall: detached" in plugin._button.toolTip()


def test_unreachable_repo_still_reports_branch(qtbot):
    checker = FakeChecker(CheckResult(repos=(
        repo("lightfall-pipelines", branch="feat/x", ahead=None, behind=None,
             remote_head=None),)))
    plugin = make(qtbot, checker)
    assert plugin.is_visible
    tip = plugin._button.toolTip()
    assert "lightfall-pipelines: feat/x" in tip
    assert "ahead" not in tip


def test_hidden_when_checker_inactive(qtbot):
    checker = FakeChecker(CheckResult(repos=(repo("lightfall", dirty=True),)))
    plugin = make(qtbot, checker)
    assert plugin.is_visible
    checker.publish(CheckResult.EMPTY)
    assert not plugin.is_visible


def test_dialog_lists_repos_read_only(qtbot):
    repos = [repo("lightfall-deck", branch="feat/x", dirty=True), repo("lightfall", ahead=1)]
    dlg = GitStateDialog(repos)
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 2
    assert dlg.table.item(0, 0).text() == "lightfall-deck"
    assert dlg.table.item(0, 1).text() == "feat/x"
    assert dlg.table.item(0, 2).text() == "uncommitted changes"
    assert dlg.table.item(1, 2).text() == "ahead 1"
    assert dlg.table.editTriggers() == dlg.table.EditTrigger.NoEditTriggers


def test_dialog_open_folder(qtbot, monkeypatch):
    from lightfall.ui.dialogs import git_state_dialog as mod

    opened = []
    monkeypatch.setattr(mod.QDesktopServices, "openUrl", lambda url: opened.append(url))
    target = repo("lightfall-deck", branch="feat/x")
    dlg = GitStateDialog([target])
    qtbot.addWidget(dlg)
    dlg.table.cellWidget(0, 4).click()
    assert opened[0].toLocalFile().replace("\\", "/").endswith("/src/lightfall-deck")


def test_git_state_dialog_frees_itself(qtbot):
    from PySide6.QtCore import Qt

    dlg = GitStateDialog([repo("lightfall", dirty=True)])
    qtbot.addWidget(dlg)
    assert dlg.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
