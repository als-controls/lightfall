"""Tab-surface behavior of the multi-agent Claude panel.

Session construction is stubbed out (it needs an API key, a main window and
the agent SDK); what's under test here is the panel's tab bookkeeping: the
uncloseable lightfall tab, the singleton-per-agent rule, pending badges, and
external-prompt routing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QTabBar, QWidget

from lightfall.agents.spec import AgentSpec
from lightfall.ui.panels.claude.agent_session_tab import AgentSessionTab
from lightfall.ui.panels.claude_panel import ClaudePanel


def _spec(name, scope="core"):
    return AgentSpec(
        name=name,
        description=f"d-{name}",
        prompt="p",
        scope=scope,
        source_path=Path(f"{name}.md"),
    )


@pytest.fixture
def panel(qtbot, monkeypatch):
    # Never build a real Claude session.
    monkeypatch.setattr(AgentSessionTab, "initialize", lambda self: None)
    monkeypatch.setattr(ClaudePanel, "_subscribe_to_claude_settings", lambda self: None)
    p = ClaudePanel()
    qtbot.addWidget(p)
    return p


def test_lightfall_tab_exists_and_is_uncloseable(panel):
    tabs = panel._tabs
    assert tabs.count() == 1
    assert tabs.tabText(0) == "lightfall"
    assert panel._lightfall_tab is tabs.widget(0)
    bar = tabs.tabBar()
    for side in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide):
        assert bar.tabButton(0, side) is None


def test_close_request_for_lightfall_tab_is_ignored(panel):
    panel._on_tab_close_requested(0)
    assert panel._tabs.count() == 1


def test_open_agent_tab_is_singleton_and_closeable(panel):
    panel._open_agent_tab(_spec("saxs"))
    assert [t.agent_name for t in panel._session_tabs()] == ["lightfall", "saxs"]

    # Opening the same agent again just focuses the existing tab.
    panel._open_agent_tab(_spec("saxs"))
    assert panel._tabs.count() == 2
    assert panel._tabs.currentWidget().agent_name == "saxs"

    panel._on_tab_close_requested(1)
    assert [t.agent_name for t in panel._session_tabs()] == ["lightfall"]


def test_pending_badge_only_on_unfocused_tab(panel):
    panel._open_agent_tab(_spec("saxs"))
    saxs = panel._find_tab("saxs")
    panel._tabs.setCurrentIndex(0)  # focus lightfall, saxs is in the background

    saxs.bus_pending_changed.emit(2)
    assert panel._tabs.tabText(panel._tabs.indexOf(saxs)) == "saxs (2)"
    assert saxs.agent_name == "saxs"  # identity is never renamed

    # Focusing the tab clears the badge.
    panel._tabs.setCurrentWidget(saxs)
    assert panel._tabs.tabText(panel._tabs.indexOf(saxs)) == "saxs"

    # A count arriving while focused doesn't badge.
    saxs.bus_pending_changed.emit(3)
    assert panel._tabs.tabText(panel._tabs.indexOf(saxs)) == "saxs"


def test_submit_external_prompt_targets_lightfall_tab(panel, monkeypatch):
    class _Widget(QWidget):
        def __init__(self):
            super().__init__()
            self.input_field = type("F", (), {"setText": lambda s, t: setattr(s, "text", t)})()
            self.sent = False

        def _send_query(self):
            self.sent = True

    panel._open_agent_tab(_spec("saxs"))
    panel._tabs.setCurrentWidget(panel._find_tab("saxs"))
    widget = _Widget()
    panel._lightfall_tab.claude_widget = widget
    monkeypatch.setattr(panel, "_get_main_window", lambda: None)

    assert panel.submit_external_prompt("hello") is True
    assert widget.sent is True
    assert widget.input_field.text == "hello"
    # ... and the lightfall tab was brought to the front.
    assert panel._tabs.currentWidget() is panel._lightfall_tab


def test_introspection_reports_open_tabs(panel):
    panel._open_agent_tab(_spec("saxs"))
    data = panel._get_specific_introspection_data()
    tabs = {t["agent"]: t for t in data["open_tabs"]}
    assert set(tabs) == {"lightfall", "saxs"}
    assert tabs["saxs"]["busy"] is False
    assert tabs["saxs"]["current"] is True
