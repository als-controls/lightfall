from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from claude_agent_sdk.types import ResultMessage

from lightfall.claude._internal.worker import PersistentClaudeWorker
from lightfall.ui.panels.claude.agent_registry import AgentRegistry


class _CtxStubClient:
    """Stub that yields one ResultMessage and reports context usage."""

    def __init__(self) -> None:
        self._ctx = {"totalTokens": 38000, "maxTokens": 100000,
                     "isAutoCompactEnabled": True, "percentage": 0.38}

    async def connect(self, prompt: str | None = None) -> None:
        pass

    async def query(self, prompt: str) -> None:
        pass

    async def receive_response(self):
        yield ResultMessage(
            subtype="success", duration_ms=0, duration_api_ms=0,
            is_error=False, num_turns=1, session_id="sess-1",
            total_cost_usd=0.0123, usage={"input_tokens": 10, "output_tokens": 5},
        )

    async def interrupt(self) -> None:
        pass

    async def get_context_usage(self):
        return self._ctx


def test_worker_emits_context_usage_after_result(qtbot):
    client = _CtxStubClient()
    worker = PersistentClaudeWorker(client)
    ctx: list[dict] = []
    worker.context_usage.connect(ctx.append)

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.context_usage, timeout=3000):
            worker.send_query("hi")
        assert ctx and ctx[0]["maxTokens"] == 100000
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_worker_survives_get_context_usage_error(qtbot):
    client = _CtxStubClient()

    async def boom():
        raise RuntimeError("no context endpoint")

    client.get_context_usage = boom  # type: ignore[method-assign]
    worker = PersistentClaudeWorker(client)

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")  # must still complete cleanly
    finally:
        worker.stop()
        assert worker.wait(3000)


@pytest.fixture
def _mock_sdk(monkeypatch):
    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())


def test_reset_conversation_emits_cockpit_reset(_mock_sdk, qtbot, monkeypatch):
    AgentRegistry.reset_instance()
    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False)
    with qtbot.waitSignal(agent.cockpit_reset, timeout=1000):
        agent.reset_conversation()
    AgentRegistry.reset_instance()


def test_reset_conversation_starts_fresh_session(qtbot, monkeypatch):
    """Reset must actually drop the session: forget the session id and rebuild a
    NEW client with resume/continue cleared — not just stop the worker (which
    would reuse the same client and resume the old conversation)."""
    AgentRegistry.reset_instance()
    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent

    # Each ClaudeSDKClient(...) call returns a distinct object so we can assert a
    # brand-new client is built on reset (not the original reused).
    monkeypatch.setattr(
        "lightfall.claude.agent.ClaudeSDKClient",
        MagicMock(side_effect=lambda **kw: MagicMock(name="sdk_client")),
    )
    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(
        target_window=target, require_approval=False, resume="old-session"
    )
    original_client = agent.client
    assert agent.options.resume == "old-session"
    agent._current_session_id = "live-session"  # pretend a session was running

    agent.reset_conversation()

    # Session forgotten and a fresh, non-resuming client built.
    assert agent._resume_session_id is None
    assert agent._current_session_id is None
    assert agent.options.resume is None
    assert agent.options.continue_conversation is False
    assert agent.client is not original_client
    AgentRegistry.reset_instance()
