"""ClashNotifier: one dialog for code clashes, a toast for shadowed agents/skills."""
from __future__ import annotations

import threading

import pytest
from PySide6.QtWidgets import QWidget

from lightfall.content.clashes import Clash, ClashLog
from lightfall.ui.content_clashes import ClashNotifier

PLAN = Clash("plan", "xafs_scan", "package", "branch:7.0.1.1", False)
PLUGIN = Clash("plugin", "tools", "core", "local", True)
AGENT = Clash("agent", "helper", "branch:7.0.1.1", "package", False)


@pytest.fixture(autouse=True)
def fresh_log():
    ClashLog.reset_instance()
    yield
    ClashLog.reset_instance()


@pytest.fixture
def window(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    return w


def make(window, dialogs, toasts, delay_ms=10, armed=True):
    notifier = ClashNotifier(
        window,
        show_dialog=lambda title, text: dialogs.append((title, text)),
        toast=lambda title, text: toasts.append((title, text)),
        delay_ms=delay_ms,
    )
    if armed:
        notifier.arm()
    return notifier


def test_code_clashes_batch_into_one_dialog(qtbot, window):
    dialogs, toasts = [], []
    make(window, dialogs, toasts)
    ClashLog.get_instance().report([PLAN])
    ClashLog.get_instance().report([PLUGIN])
    qtbot.waitUntil(lambda: bool(dialogs), timeout=2000)
    qtbot.wait(50)
    assert len(dialogs) == 1
    title, text = dialogs[0]
    assert title == "Content not loaded"
    assert "plan xafs_scan from branch:7.0.1.1 was not loaded: package has one with the same name" in text
    assert "plugin tools from local was not loaded: core has one with the same name" in text
    assert not toasts


def test_agent_clashes_toast(qtbot, window):
    dialogs, toasts = [], []
    make(window, dialogs, toasts)
    ClashLog.get_instance().report([AGENT, Clash("skill", "s", "local", "package", False)])
    qtbot.waitUntil(lambda: bool(toasts), timeout=2000)
    assert len(toasts) == 1
    assert toasts[0][0] == "Content shadowed"
    assert "agent helper from branch:7.0.1.1 shadows the one from package" in toasts[0][1]
    assert "skill s from package shadows the one from local" not in toasts[0][1]
    assert "skill s from local shadows the one from package" in toasts[0][1]
    assert not dialogs


def test_dialog_shows_each_clash_once(qtbot, window):
    dialogs, toasts = [], []
    make(window, dialogs, toasts)
    ClashLog.get_instance().report([PLAN])
    qtbot.waitUntil(lambda: bool(dialogs), timeout=2000)
    ClashLog.get_instance().report([PLAN])  # same clash found again (reload)
    qtbot.wait(100)
    assert len(dialogs) == 1


def test_startup_clashes_shown_after_construction(qtbot, window):
    ClashLog.get_instance().report([PLAN, AGENT])
    dialogs, toasts = [], []
    make(window, dialogs, toasts)
    assert not dialogs and not toasts
    qtbot.waitUntil(lambda: bool(dialogs and toasts), timeout=2000)
    assert "xafs_scan" in dialogs[0][1]


def test_no_clashes_no_dialog(qtbot, window):
    dialogs, toasts = [], []
    make(window, dialogs, toasts)
    qtbot.wait(100)
    assert not dialogs and not toasts


def test_long_lists_are_capped(qtbot, window):
    dialogs, toasts = [], []
    make(window, dialogs, toasts)
    ClashLog.get_instance().report(
        [Clash("plan", f"p{i}", "core", "local", False) for i in range(25)]
    )
    qtbot.waitUntil(lambda: bool(dialogs), timeout=2000)
    text = dialogs[0][1]
    assert "p19" in text and "p20" not in text
    assert "and 5 more" in text


def test_report_from_a_worker_thread_is_delivered(qtbot, window):
    dialogs, toasts = [], []
    ClashLog.get_instance()  # created on the GUI thread
    make(window, dialogs, toasts)
    t = threading.Thread(target=lambda: ClashLog.get_instance().report([PLAN]))
    t.start()
    t.join()
    qtbot.waitUntil(lambda: bool(dialogs), timeout=2000)


def test_nothing_shown_until_armed(qtbot, window):
    ClashLog.get_instance().report([PLAN, AGENT])  # found during startup
    dialogs, toasts = [], []
    notifier = make(window, dialogs, toasts, armed=False)
    ClashLog.get_instance().report([PLUGIN])  # found in the post-login wave
    qtbot.wait(100)
    assert not dialogs and not toasts

    notifier.arm()
    qtbot.waitUntil(lambda: bool(dialogs and toasts), timeout=2000)
    qtbot.wait(50)
    assert len(dialogs) == 1 and len(toasts) == 1
    assert "xafs_scan" in dialogs[0][1] and "tools" in dialogs[0][1]
    assert "helper" in toasts[0][1]


def test_arm_without_clashes_shows_nothing_and_is_idempotent(qtbot, window):
    dialogs, toasts = [], []
    notifier = make(window, dialogs, toasts, armed=False)
    notifier.arm()
    notifier.arm()
    qtbot.wait(50)
    assert not dialogs and not toasts
    ClashLog.get_instance().report([PLAN])
    qtbot.waitUntil(lambda: bool(dialogs), timeout=2000)
    assert len(dialogs) == 1


def test_default_toast_is_parented_to_the_window(qtbot, window, monkeypatch):
    from lightfall.ui import toast as toast_mod

    calls = []

    class FakeManager:
        def warning(self, title, text="", duration=None, parent=None):
            calls.append(parent)

    monkeypatch.setattr(
        toast_mod.ToastManager, "get_instance", classmethod(lambda cls: FakeManager())
    )
    notifier = ClashNotifier(window, show_dialog=lambda t, x: None, delay_ms=10)
    notifier.arm()
    ClashLog.get_instance().report([AGENT])
    qtbot.waitUntil(lambda: bool(calls), timeout=2000)
    assert calls == [window]


def test_dialog_ends_with_rename_hint(qtbot, window):
    dialogs, toasts = [], []
    make(window, dialogs, toasts)
    ClashLog.get_instance().report([PLAN])
    qtbot.waitUntil(lambda: bool(dialogs), timeout=2000)
    assert dialogs[0][1].splitlines()[-1] == (
        "Rename the item that was not loaded to use it."
    )


def test_code_source_labels():
    from lightfall.content.clashes import code_source

    class Core:
        pass

    Core.__module__ = "lightfall.plugins.agents.tools"

    class Pkg:
        pass

    Pkg.__module__ = "lightfall_deck.tools"
    assert code_source(Core) == "core"
    assert code_source(Core()) == "core"
    assert code_source(Pkg) == "package"
    assert code_source(None) == "built-in"
