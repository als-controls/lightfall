"""Voice queue across widget rebuilds, deferred reload, tab close, panel lookup.

The queue is owned by the ``AgentSessionTab`` and handed to each new
``ClaudeAssistantWidget``; it survives a rebuild only when the new widget
resumes the same conversation. The real widget is built around a fake agent
(as in ``tests/claude/test_submit_user_prompt.py``); the panel tests stub
session construction.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

from lightfall.agents.backends import info as info_mod
from lightfall.agents.spec import AgentSpec
from lightfall.claude import widget as widget_mod
from lightfall.claude.voice_queue import VoiceQueue
from lightfall.ui.panels.claude import agent_session_tab as tab_mod
from lightfall.ui.panels.claude.agent_session_tab import AgentSessionTab
from lightfall.ui.panels.claude_panel import ClaudePanel
from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider
from tests.claude.test_submit_user_prompt import FakeAgent, _Info


class SidAgent(FakeAgent):
    """FakeAgent that knows which conversation it is in."""

    def __init__(self, *args, resume=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._resume_session_id = resume
        self._current_session_id = None
        self.bus_name = None

    @property
    def current_session_id(self):
        return self._current_session_id or self._resume_session_id

    def stop(self, wait_ms=5000):
        self.busy = False


class _Store:
    def load_transcript(self, session_id):
        return []


@pytest.fixture
def build_env(qtbot, monkeypatch):
    """Let ``AgentSessionTab`` build a real widget around ``SidAgent``."""
    from lightfall.agents.bus import AgentBus

    AgentBus.reset_instance()
    monkeypatch.setattr(widget_mod, "QtClaudeAgent", SidAgent)
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: _Info())
    main = QWidget()
    qtbot.addWidget(main)
    monkeypatch.setattr(tab_mod, "find_main_window", lambda w: main)
    monkeypatch.setattr(tab_mod, "build_ncs_system_prompt", lambda: "")
    monkeypatch.setattr(tab_mod, "session_model", lambda: None)
    monkeypatch.setattr(tab_mod, "session_effort", lambda: None)
    monkeypatch.setattr(tab_mod, "_session_store", lambda: _Store())
    prefs = {"auto_restore": False, "last": None}
    for name, value in {
        "is_configured": lambda: True,
        "get_permission_mode": lambda: "default",
        "get_api_key": lambda: None,
        "get_base_url": lambda: None,
        "get_disable_betas": lambda: False,
        "get_auto_restore": lambda: prefs["auto_restore"],
        "get_last_session_id": lambda *a, **k: prefs["last"],
    }.items():
        monkeypatch.setattr(ClaudeSettingsProvider, name, staticmethod(value))
    yield prefs
    AgentBus.reset_instance()


def _flush_deletes():
    """Run pending zero-delay timers, then the deferred deletes (as the event loop does)."""
    QCoreApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


def _labels(widget) -> list[str]:
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


def test_rebuild_same_session_keeps_queue(qtbot, build_env):
    build_env.update(auto_restore=True, last="S1")
    tab = AgentSessionTab()
    qtbot.addWidget(tab)
    tab.initialize()
    old = tab.claude_widget
    assert old.voice_queue is tab.voice_queue
    assert old.submit_user_prompt("zero") == "sent"  # now busy
    old.submit_user_prompt("one")
    old.submit_user_prompt("two")
    QCoreApplication.processEvents()  # let the old widget's scroll timers run
    assert len(tab.voice_queue) == 2
    tab.voice_queue.pause()  # e.g. after an error; otherwise it drains (below)

    tab.reload_agent()
    _flush_deletes()

    new = tab.claude_widget
    assert new is not old
    assert new.voice_queue is tab.voice_queue
    assert [it.text for it in tab.voice_queue.items()] == ["one", "two"]
    # The tab-owned queue outlived the old widget's deletion.
    tab.voice_queue.push("three")
    assert len(new._voice_queue_strip.item_rows()) == 3


def test_rebuild_new_session_drops_with_notice(qtbot, build_env):
    tab = AgentSessionTab()
    qtbot.addWidget(tab)
    tab.initialize()
    old = tab.claude_widget
    old.agent._current_session_id = "S1"
    assert old.submit_user_prompt("zero") == "sent"  # now busy
    old.submit_user_prompt("one")
    old.submit_user_prompt("two")
    QCoreApplication.processEvents()  # let the old widget's scroll timers run

    tab.reload_agent()  # auto-restore off: the new widget starts a new conversation
    _flush_deletes()

    new = tab.claude_widget
    assert len(tab.voice_queue) == 0
    assert new.voice_queue is tab.voice_queue
    notices = [t for t in _labels(new) if "queued voice message" in t]
    assert len(notices) == 1 and "2 queued" in notices[0]
    assert notices[0] == "2 queued voice message(s) discarded: new conversation"


def test_restore_other_session_drops_queue(qtbot, build_env):
    tab = AgentSessionTab()
    qtbot.addWidget(tab)
    tab.initialize()
    tab.claude_widget.agent._current_session_id = "S1"
    tab.claude_widget.submit_user_prompt("zero")  # sent; now busy
    tab.claude_widget.submit_user_prompt("one")
    QCoreApplication.processEvents()  # let the old widget's scroll timers run

    tab.restore_session("S2")
    _flush_deletes()
    assert len(tab.voice_queue) == 0
    assert tab.claude_widget.agent.current_session_id == "S2"


def test_restore_same_session_keeps_queue(qtbot, build_env):
    tab = AgentSessionTab()
    qtbot.addWidget(tab)
    tab.initialize()
    tab.claude_widget.agent._current_session_id = "S1"
    tab.claude_widget.submit_user_prompt("zero")  # sent; now busy
    tab.claude_widget.submit_user_prompt("one")
    QCoreApplication.processEvents()  # let the old widget's scroll timers run
    tab.voice_queue.pause()  # keep it from draining into the new widget

    tab.restore_session("S1")
    assert [it.text for it in tab.voice_queue.items()] == ["one"]
    assert tab.claude_widget.voice_queue is tab.voice_queue


def test_handed_over_queue_drains_into_new_widget(qtbot, build_env):
    build_env.update(auto_restore=True, last="S1")
    tab = AgentSessionTab()
    qtbot.addWidget(tab)
    tab.initialize()
    tab.claude_widget.submit_user_prompt("zero")  # sent; now busy
    tab.claude_widget.submit_user_prompt("one")
    QCoreApplication.processEvents()  # let the old widget's scroll timers run

    tab.reload_agent()
    new = tab.claude_widget
    qtbot.waitUntil(lambda: new.agent.sent == ["one"], timeout=2000)
    assert len(tab.voice_queue) == 0


# --- panel --------------------------------------------------------------------


class _FakeAgent(QObject):
    query_completed = Signal()
    query_cancelled = Signal()
    error_occurred = Signal(str)

    def __init__(self, busy=False):
        super().__init__()
        self.busy = busy

    def is_busy(self):
        return self.busy


@pytest.fixture
def bare_panel(qtbot, monkeypatch):
    monkeypatch.setattr(ClaudePanel, "_setup_ui", lambda self: None)
    p = ClaudePanel()
    qtbot.addWidget(p)
    return p


def test_deferred_reload_waits_for_queue(bare_panel, monkeypatch, qtbot):
    panel = bare_panel
    monkeypatch.setattr(ClaudePanel, "_DEFERRED_POLL_MS", 10)
    reloads = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: reloads.append(1))
    agent = _FakeAgent(busy=True)
    queue = VoiceQueue()
    queue.push("queued one")
    panel._claude_widget = MagicMock()
    panel._claude_widget.agent = agent
    panel._claude_widget.voice_queue = queue

    panel._reload_tab_when_idle(None)
    agent.busy = False
    agent.query_completed.emit()  # the turn ended, but a voice prompt waits
    qtbot.wait(80)
    assert reloads == []

    queue.pop_next()  # the queue drained
    qtbot.waitUntil(lambda: reloads == [1], timeout=2000)


def test_deferred_reload_idle_with_queue_still_waits(bare_panel, monkeypatch, qtbot):
    panel = bare_panel
    monkeypatch.setattr(ClaudePanel, "_DEFERRED_POLL_MS", 10)
    reloads = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: reloads.append(1))
    queue = VoiceQueue()
    queue.push("queued one")
    panel._claude_widget = MagicMock()
    panel._claude_widget.agent = _FakeAgent(busy=False)
    panel._claude_widget.voice_queue = queue

    panel._reload_tab_when_idle(None)
    qtbot.wait(60)
    assert reloads == []
    queue.pop_next()
    qtbot.waitUntil(lambda: reloads == [1], timeout=2000)


def test_deferred_reload_ignores_paused_queue(bare_panel, monkeypatch):
    panel = bare_panel
    reloads = []
    monkeypatch.setattr(panel, "_reload_agent", lambda: reloads.append(1))
    queue = VoiceQueue()
    queue.push("queued one")
    queue.pause()
    panel._claude_widget = MagicMock()
    panel._claude_widget.agent = _FakeAgent(busy=False)
    panel._claude_widget.voice_queue = queue

    panel._reload_tab_when_idle(None)
    assert reloads == [1]


def _spec(name):
    return AgentSpec(
        name=name, description=f"d-{name}", prompt="p", scope="core",
        source_path=Path(f"{name}.md"),
    )


@pytest.fixture
def tab_panel(qtbot, monkeypatch):
    from lightfall.agents.bus import AgentBus

    monkeypatch.setattr(AgentSessionTab, "initialize", lambda self: None)
    monkeypatch.setattr(ClaudePanel, "_subscribe_to_claude_settings", lambda self: None)
    AgentBus.reset_instance()
    p = ClaudePanel()
    qtbot.addWidget(p)
    yield p
    AgentBus.reset_instance()


def test_close_tab_with_queue_confirms(tab_panel, monkeypatch):
    panel = tab_panel
    tab = panel._open_agent_tab(_spec("saxs"))
    tab.voice_queue.push("a")
    tab.voice_queue.push("b")
    asked = []
    answer = [QMessageBox.StandardButton.No]

    def fake_question(parent, title, text, *args, **kwargs):
        asked.append(text)
        return answer[0]

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    index = panel._tabs.indexOf(tab)
    panel._on_tab_close_requested(index)
    assert asked == ["Discard 2 queued voice messages?"]
    assert tab in panel._session_tabs()

    answer[0] = QMessageBox.StandardButton.Yes
    panel._on_tab_close_requested(panel._tabs.indexOf(tab))
    assert tab not in panel._session_tabs()


def test_close_tab_without_queue_does_not_ask(tab_panel, monkeypatch):
    panel = tab_panel
    tab = panel._open_agent_tab(_spec("saxs"))

    def boom(*a, **k):
        raise AssertionError("should not ask")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(boom))
    panel._on_tab_close_requested(panel._tabs.indexOf(tab))
    assert tab not in panel._session_tabs()


def test_quit_discards_queue_with_log(tab_panel, monkeypatch):
    panel = tab_panel
    tab = panel._open_agent_tab(_spec("saxs"))
    tab.voice_queue.push("a")
    messages = []
    from lightfall.ui.panels import claude_panel as panel_mod

    monkeypatch.setattr(
        panel_mod.logger, "info", lambda msg, *a, **k: messages.append(msg.format(*a))
    )
    panel._on_closing()
    assert len(tab.voice_queue) == 0
    assert any("1 queued voice message" in m for m in messages)


def test_current_assistant_widget_tracks_tab(tab_panel):
    panel = tab_panel
    main_w, saxs_w = QWidget(), QWidget()
    panel._lightfall_tab.claude_widget = main_w
    tab = panel._open_agent_tab(_spec("saxs"))
    tab.claude_widget = saxs_w

    assert panel.current_assistant_widget() is saxs_w
    panel._tabs.setCurrentWidget(panel._lightfall_tab)
    assert panel.current_assistant_widget() is main_w
    assert panel.assistant_widgets() == [main_w, saxs_w]

    tab.claude_widget = None
    panel._tabs.setCurrentWidget(tab)
    assert panel.current_assistant_widget() is None
    assert panel.assistant_widgets() == [main_w]
