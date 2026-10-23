"""App exit stops every live Claude agent (GlitchTip LIGHTFALL-5).

Docked panels never get a closeEvent when the main window closes, so the
Claude panel's _on_closing never ran on exit and no session was stopped. The
SDK's atexit hook then killed each still-connected CLI and its message reader
logged "Fatal error in message reader". _cleanup_on_exit now signals every
live agent and joins the workers against one shared deadline, so the exit
watchdog (0xC0000005 when it fires mid-teardown) still has budget left.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.claude import agent as agent_mod
from lightfall.claude.agent import QtClaudeAgent
from lightfall.ui.panels.claude.tool_registry import ToolRegistry


@pytest.fixture(autouse=True)
def reset_registry():
    ToolRegistry.reset_instance()
    yield
    ToolRegistry.reset_instance()


@pytest.fixture
def mock_sdk(monkeypatch):
    """Replace ClaudeSDKClient so __init__ doesn't spawn/connect the CLI."""
    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.tool_registry.ToolRegistry._read_list_pref",
        lambda self, key: None,
    )
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.tool_registry.ToolRegistry._migrate_legacy_pref_if_needed",
        lambda self: None,
    )


def _agent_with_fake_worker():
    agent = QtClaudeAgent.__new__(QtClaudeAgent)  # skip heavy __init__
    worker = MagicMock()
    worker.isRunning.return_value = True
    agent._worker = worker
    agent._is_connected = True
    return agent, worker


def test_constructed_agent_is_tracked(mock_sdk, qtbot):
    from PySide6.QtWidgets import QWidget

    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False)
    assert agent in agent_mod._live_agents


def test_stop_all_agents_signals_every_worker_without_joining(monkeypatch):
    a, wa = _agent_with_fake_worker()
    b, wb = _agent_with_fake_worker()
    monkeypatch.setattr(agent_mod, "_live_agents", {a, b})

    workers = agent_mod.stop_all_agents()

    for w in (wa, wb):
        w.stop.assert_called_once()
        w.wait.assert_not_called()
    assert set(workers) == {wa, wb}
    assert a._is_connected is False and b._is_connected is False


def test_stop_all_agents_skips_idle_agents(monkeypatch):
    a, wa = _agent_with_fake_worker()
    wa.isRunning.return_value = False
    monkeypatch.setattr(agent_mod, "_live_agents", {a})

    assert agent_mod.stop_all_agents() == []
    wa.stop.assert_not_called()


def test_wait_for_workers_shares_one_deadline(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(agent_mod.time, "monotonic", lambda: clock[0])

    def stuck_wait(ms):
        clock[0] += ms / 1000  # never finishes: burns its whole budget
        return False

    w1, w2 = MagicMock(), MagicMock()
    w1.wait.side_effect = stuck_wait
    w2.wait.side_effect = stuck_wait

    assert agent_mod.wait_for_workers([w1, w2], timeout_ms=1500) is False
    w1.wait.assert_called_once_with(1500)
    w2.wait.assert_not_called()  # budget already spent
    assert clock[0] - 100.0 == pytest.approx(1.5)


def test_wait_for_workers_true_when_all_finish(monkeypatch):
    w1, w2 = MagicMock(), MagicMock()
    w1.wait.return_value = True
    w2.wait.return_value = True
    assert agent_mod.wait_for_workers([w1, w2], timeout_ms=1000) is True
    w2.wait.assert_called_once()
