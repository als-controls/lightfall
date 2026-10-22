"""Per-tab model/effort: a secondary tab's picks drive its build and its menu."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lightfall.ui.panels.claude import agent_session_tab as tab_module
from lightfall.ui.panels.claude.agent_session_tab import AgentSessionTab


@pytest.fixture
def tab(qtbot, monkeypatch):
    monkeypatch.setattr(tab_module, "session_model", lambda: "pref-model")
    monkeypatch.setattr(tab_module, "session_effort", lambda: "medium")
    t = AgentSessionTab()
    qtbot.addWidget(t)
    return t


def test_no_picks_follow_the_preference(tab):
    assert tab._build_model() == "pref-model"
    assert tab._build_effort() == "medium"


def test_picks_drive_the_build(tab, monkeypatch):
    from lightfall.agents.backends import info as info_module

    monkeypatch.setattr(info_module, "backend_info",
                        lambda: SimpleNamespace(live_model_id=lambda p: f"live:{p}"))
    tab.set_model_preset("claude-opus")
    tab.set_effort_level("low")
    assert tab._build_model() == "live:claude-opus"
    assert tab._build_effort() == "low"
    assert tab.effective_effort() == "low"


def test_explicit_default_effort_builds_with_none(tab):
    tab.set_effort_level("")  # the menu's "Default" entry
    assert tab._build_effort() is None
    assert tab.effective_effort() == ""


def test_picks_reach_the_widget_menu(tab):
    widget = SimpleNamespace(model_override=None, effort_override=None)
    tab.claude_widget = widget
    tab.set_model_preset("claude-opus")
    tab.set_effort_level("high")
    assert (widget.model_override, widget.effort_override) == ("claude-opus", "high")
