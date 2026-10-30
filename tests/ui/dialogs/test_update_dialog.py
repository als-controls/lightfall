"""Tests for the update dialog: rows, blockers, the busy gate, apply results, restart."""

from __future__ import annotations

from lightfall.ui.dialogs import update_dialog as dialog_mod
from lightfall.ui.dialogs.update_dialog import UpdateDialog
from lightfall.updates.apply import ItemResult
from lightfall.updates.model import CheckResult
from lightfall.updates.pypi import PypiState
from tests.updates.fakes import FakeChecker, repo

RESULT = CheckResult(
    repos=(repo("lightfall", behind=2, remote_head="bbb2222"),
           repo("lightfall-deck", behind=1, dirty=True)),
    pypi=(PypiState("lightfall-saxs", "1.0", "1.1"),),
)


def make(qtbot, checker=None, **kw):
    kw.setdefault("busy", lambda: False)
    kw.setdefault("ask_restart", lambda parent: False)
    dlg = UpdateDialog(checker or FakeChecker(RESULT), **kw)
    qtbot.addWidget(dlg)
    return dlg


def status(dlg, name):
    return dlg.status_text(name)


def test_rows_and_versions(qtbot):
    dlg = make(qtbot)
    assert dlg.row_names() == ["lightfall", "lightfall-deck", "lightfall-saxs"]
    assert dlg.cell("lightfall", "Available") == "bbb2222 (2 commit(s) behind)"
    assert dlg.cell("lightfall-saxs", "Current") == "1.0"
    assert dlg.cell("lightfall-saxs", "Available") == "1.1"
    assert dlg.cell("lightfall", "Type") == "source"


def test_blocked_row_checkbox_disabled(qtbot):
    dlg = make(qtbot)
    assert not dlg.is_checkable("lightfall-deck")
    assert not dlg.is_checked("lightfall-deck")
    assert "uncommitted changes" in status(dlg, "lightfall-deck")
    assert dlg.is_checked("lightfall") and dlg.is_checked("lightfall-saxs")


def test_busy_disables_update(qtbot):
    dlg = make(qtbot, busy=lambda: True)
    dlg.show()
    assert dlg._banner.isVisible()
    assert "A scan is running" in dlg._banner.text()
    assert not dlg._update_button.isEnabled()


def test_check_now_button(qtbot):
    checker = FakeChecker(RESULT)
    dlg = make(qtbot, checker)
    dlg._check_button.click()
    assert checker.checks == [True]


def test_partial_failure_shown_and_restart_offered(qtbot):
    checker = FakeChecker(RESULT)
    seen = {}

    def apply(repos, pypi, log=lambda s: None):
        seen["repos"] = [r.name for r in repos]
        seen["pypi"] = [p.name for p in pypi]
        log("$ git pull")
        return [ItemResult("lightfall", True, True, "updated"),
                ItemResult("lightfall-saxs", False, False, "pip upgrade failed", "boom")]

    asked = []
    dlg = make(qtbot, checker, apply=apply, ask_restart=lambda p: asked.append(p) or False)
    dlg._update_button.click()
    qtbot.waitUntil(lambda: status(dlg, "lightfall") == "Updated", timeout=5000)
    assert seen == {"repos": ["lightfall"], "pypi": ["lightfall-saxs"]}
    assert status(dlg, "lightfall-saxs") == "Failed: pip upgrade failed"
    assert asked == [dlg]
    assert checker.restart_pending
    assert checker.checks == [False]
    assert "$ git pull" in dlg._log.toPlainText()


def test_restart_requested_when_accepted(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(dialog_mod, "request_restart", lambda: calls.append(1))

    def apply(repos, pypi, log=lambda s: None):
        return [ItemResult("lightfall", True, True, "updated")]

    dlg = make(qtbot, apply=apply, ask_restart=lambda p: True)
    dlg._update_button.click()
    qtbot.waitUntil(lambda: calls == [1], timeout=5000)


def test_no_restart_while_busy_after_update(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(dialog_mod, "request_restart", lambda: calls.append(1))
    state = {"busy": False}
    checker = FakeChecker(RESULT)

    def apply(repos, pypi, log=lambda s: None):
        state["busy"] = True  # a scan started while we were updating
        return [ItemResult("lightfall", True, True, "updated")]

    dlg = make(qtbot, checker, apply=apply, busy=lambda: state["busy"],
               ask_restart=lambda p: True)
    dlg._update_button.click()
    qtbot.waitUntil(lambda: checker.restart_pending, timeout=5000)
    assert calls == []


def test_update_refused_without_permission(qtbot):
    called = []
    checker = FakeChecker(RESULT, allowed=False)
    dlg = make(qtbot, checker, apply=lambda *a, **k: called.append(1) or [])
    dlg._update_button.click()
    qtbot.wait(100)
    assert called == []
