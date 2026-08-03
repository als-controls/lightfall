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


def test_close_guard_follows_the_tab_not_the_index(panel):
    # Tabs are movable: after a drag, index 0 may hold an ordinary session and
    # the lightfall tab may sit anywhere. The guard must key on identity.
    panel._open_agent_tab(_spec("saxs"))
    panel._tabs.tabBar().moveTab(0, 1)  # saxs -> index 0, lightfall -> index 1
    assert panel._tabs.widget(0).agent_name == "saxs"
    assert panel._tabs.indexOf(panel._lightfall_tab) == 1

    # lightfall at a nonzero index still cannot be closed...
    panel._on_tab_close_requested(1)
    assert panel._tabs.count() == 2

    # ...and the ordinary tab at index 0 can.
    panel._on_tab_close_requested(0)
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


def test_programmatic_sends_target_lightfall_tab(panel, monkeypatch):
    class _Widget(QWidget):
        def __init__(self):
            super().__init__()
            # Real input_field is a QPlainTextEdit: setPlainText, NOT setText.
            self.input_field = type(
                "F", (), {"setPlainText": lambda s, t: setattr(s, "text", t)})()
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

    # action_send_message (logbook "send to Claude", skill triggers) routes the
    # same way -- it must not dispatch invisibly from another tab.
    widget.sent = False
    panel._tabs.setCurrentWidget(panel._find_tab("saxs"))
    assert panel.action_send_message("from logbook") is True
    assert widget.sent is True
    assert widget.input_field.text == "from logbook"
    assert panel._tabs.currentWidget() is panel._lightfall_tab


def test_introspection_reports_open_tabs(panel):
    panel._open_agent_tab(_spec("saxs"))
    data = panel._get_specific_introspection_data()
    tabs = {t["agent"]: t for t in data["open_tabs"]}
    assert set(tabs) == {"lightfall", "saxs"}
    assert tabs["saxs"]["busy"] is False
    assert tabs["saxs"]["current"] is True


def _prime_registry(monkeypatch, specs):
    from lightfall.agents.registry import AgentSpecRegistry
    reg = AgentSpecRegistry.get_instance()
    by_name = {s.name: s for s in specs}
    monkeypatch.setattr(reg, "get", lambda name: by_name.get(name))
    monkeypatch.setattr(reg, "enabled_specs", lambda: list(specs))
    return reg


def test_action_open_agent_tab_unknown_agent(panel, monkeypatch):
    _prime_registry(monkeypatch, [_spec("lightfall"), _spec("saxs")])
    result = panel.action_open_agent_tab("nope")
    assert result["success"] is False
    assert "saxs" in result["error"]


def test_action_open_agent_tab_not_openable(panel, monkeypatch):
    from dataclasses import replace
    observer = replace(_spec("observer"), openable=False)
    _prime_registry(monkeypatch, [_spec("lightfall"), observer])
    result = panel.action_open_agent_tab("observer")
    assert result["success"] is False
    assert "openable" in result["error"]


def test_action_open_agent_tab_opens_then_focuses(panel, monkeypatch):
    _prime_registry(monkeypatch, [_spec("lightfall"), _spec("saxs")])
    result = panel.action_open_agent_tab("saxs")
    assert result == {"success": True, "agent": "saxs",
                      "focused_existing": False, "message_queued": False}
    assert panel._tabs.currentWidget() is panel._find_tab("saxs")

    panel._tabs.setCurrentWidget(panel._lightfall_tab)
    result = panel.action_open_agent_tab("saxs")
    assert result["focused_existing"] is True
    assert panel._tabs.count() == 2  # no duplicate
    assert panel._tabs.currentWidget() is panel._find_tab("saxs")


def test_action_open_agent_tab_message_delivered_on_widget_created(panel, monkeypatch):
    _prime_registry(monkeypatch, [_spec("lightfall"), _spec("saxs")])
    result = panel.action_open_agent_tab("saxs", message="start the fit")
    assert result["message_queued"] is True

    tab = panel._find_tab("saxs")
    fake = type("W", (), {})()
    fake.input_field = type(
        "F", (), {"setPlainText": lambda s, t: setattr(s, "text", t)})()
    fake.sent = False
    fake._send_query = lambda f=fake: setattr(f, "sent", True)

    tab.widget_created.emit(fake)
    assert fake.sent is True
    assert fake.input_field.text == "start the fit"

    # One-shot: a second emission must not resend.
    fake.sent = False
    tab.widget_created.emit(fake)
    assert fake.sent is False
