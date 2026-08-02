from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lightfall.ui.panels.claude.tool_registry import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_registry():
    ToolRegistry.reset_instance()
    yield
    ToolRegistry.reset_instance()


@pytest.fixture
def _mock_sdk(monkeypatch):
    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())


def _make_agent(qtbot, **kwargs):
    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    return QtClaudeAgent(target_window=target, require_approval=False, **kwargs)


def test_model_and_effort_flow_into_options(_mock_sdk, qtbot):
    agent = _make_agent(qtbot, model="opus", effort="high")
    assert agent.options.model == "opus"
    assert agent.options.effort == "high"


def test_resume_flows_into_options(_mock_sdk, qtbot):
    agent = _make_agent(qtbot, resume="sess-abc")
    assert agent.options.resume == "sess-abc"


def test_defaults_leave_model_unset_but_pin_cwd(_mock_sdk, qtbot):
    from lightfall.claude.agent import lightfall_agent_cwd

    agent = _make_agent(qtbot)
    assert agent.options.model is None
    assert agent.options.effort is None
    assert agent.options.cwd == lightfall_agent_cwd()
    assert Path(lightfall_agent_cwd()).is_dir()
