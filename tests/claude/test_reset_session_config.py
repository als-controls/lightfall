from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from lightfall.ui.panels.claude.tool_registry import ToolRegistry


def test_reset_conversation_updates_session_config(qtbot, monkeypatch):
    """reset_conversation must keep _session_config in sync with the rebuilt options."""
    ToolRegistry.reset_instance()
    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent

    monkeypatch.setattr(
        "lightfall.claude.agent.ClaudeSDKClient",
        MagicMock(side_effect=lambda **kw: MagicMock(name="sdk_client")),
    )
    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False, resume="sess-old")
    assert agent._session_config.resume == "sess-old"

    agent.reset_conversation()

    assert agent._session_config.resume is None
    assert agent._session_config.skills_dir == Path(agent.options.plugins[0]["path"])
    ToolRegistry.reset_instance()
