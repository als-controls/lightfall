"""Shutdown must not block on Claude workers.

Regression tests for the exit crash (0xC0000005): the phase-4 close path
called agent.stop(), which joins the worker QThread for up to 5 s per
session on the GUI thread — exceeding the shutdown watchdog and forcing
os._exit() mid-teardown. On shutdown we signal the worker and abandon it
(pre-phase-4 semantics); the graceful wait remains for interactive paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from lightfall.claude.agent import QtClaudeAgent
from lightfall.ui.panels.claude.agent_session_tab import AgentSessionTab


def _agent_with_fake_worker():
    agent = QtClaudeAgent.__new__(QtClaudeAgent)  # skip heavy __init__
    worker = MagicMock()
    worker.isRunning.return_value = True
    worker.wait.return_value = True
    agent._worker = worker
    agent._is_connected = True
    return agent, worker


def test_stop_default_waits_for_worker():
    agent, worker = _agent_with_fake_worker()
    agent.stop()
    worker.stop.assert_called_once()
    worker.wait.assert_called_once_with(5000)


def test_stop_wait_ms_zero_signals_but_never_joins():
    agent, worker = _agent_with_fake_worker()
    agent.stop(wait_ms=0)
    worker.stop.assert_called_once()
    worker.wait.assert_not_called()
    assert agent._is_connected is False


def test_tab_close_session_no_wait_propagates(qtbot):
    tab = AgentSessionTab.__new__(AgentSessionTab)
    fake_agent = MagicMock()
    fake_widget = MagicMock()
    fake_widget.agent = fake_agent
    tab.claude_widget = fake_widget
    tab._layout = MagicMock()
    tab.is_agent_ready = True
    # widget_destroyed is a class-level Signal; emitting on a __new__
    # instance without QObject.__init__ is invalid, so stub the emit path.
    tab.widget_destroyed = MagicMock()

    tab.close_session(wait_for_worker=False)

    fake_agent.stop.assert_called_once_with(wait_ms=0)


def test_tab_close_session_default_is_graceful(qtbot):
    tab = AgentSessionTab.__new__(AgentSessionTab)
    fake_agent = MagicMock()
    fake_widget = MagicMock()
    fake_widget.agent = fake_agent
    tab.claude_widget = fake_widget
    tab._layout = MagicMock()
    tab.is_agent_ready = True
    tab.widget_destroyed = MagicMock()

    tab.close_session()

    fake_agent.stop.assert_called_once_with(wait_ms=5000)
