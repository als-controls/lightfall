"""ClaudePanel.current_session_changed: plugins follow the visible session tab."""

from __future__ import annotations

from pathlib import Path

import pytest

from lightfall.agents.spec import AgentSpec
from lightfall.ui.panels.claude.agent_session_tab import AgentSessionTab
from lightfall.ui.panels.claude_panel import ClaudePanel


@pytest.fixture
def panel(qtbot, monkeypatch):
    from lightfall.agents.bus import AgentBus

    monkeypatch.setattr(AgentSessionTab, "initialize", lambda self: None)
    monkeypatch.setattr(ClaudePanel, "_subscribe_to_claude_settings", lambda self: None)
    AgentBus.reset_instance()
    p = ClaudePanel()
    qtbot.addWidget(p)
    yield p
    AgentBus.reset_instance()


def test_switching_tabs_emits_current_session_changed(panel, qtbot):
    spec = AgentSpec(name="saxs", description="d", prompt="p", scope="core",
                     source_path=Path("saxs.md"))
    with qtbot.waitSignal(panel.current_session_changed, timeout=1000):
        panel._open_agent_tab(spec)  # opening focuses the new tab
    with qtbot.waitSignal(panel.current_session_changed, timeout=1000):
        panel._tabs.setCurrentIndex(0)
