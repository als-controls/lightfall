"""QtClaudeAgent wires the 'disable betas' setting to the CLI env var.

Azure AI Foundry (and other proxy gateways) reject the Claude CLI's default
beta headers (e.g. ``anthropic-beta: advisor-tool-2026-03-01`` -> HTTP 400).
Setting ``CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`` for the CLI subprocess
drops those headers. The long-dead ``disable_betas`` preference now drives it.
Verified on the CMS Azure Foundry backend: with this env set + a current model
(claude-sonnet-4-6 / claude-opus-4-8), the agent returns successfully.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from lightfall.ui.panels.claude.agent_registry import AgentRegistry

_ENV = "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"


@pytest.fixture(autouse=True)
def reset_registry():
    AgentRegistry.reset_instance()
    yield
    AgentRegistry.reset_instance()


@pytest.fixture
def mock_sdk(monkeypatch):
    """Replace ClaudeSDKClient so __init__ doesn't spawn/connect the CLI."""
    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())
    # No registered agents -> keep per-plugin assembly trivial.
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._read_list_pref",
        lambda self, key: None,
    )
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._migrate_legacy_pref_if_needed",
        lambda self: None,
    )


def _make_agent(qtbot, **kwargs):
    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    return QtClaudeAgent(target_window=target, require_approval=False, **kwargs)


def test_disable_betas_true_sets_env(mock_sdk, qtbot, monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    _make_agent(qtbot, disable_betas=True)
    assert os.environ.get(_ENV) == "1"


def test_disable_betas_default_leaves_env_unset(mock_sdk, qtbot, monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    _make_agent(qtbot)  # disable_betas defaults False
    assert _ENV not in os.environ
