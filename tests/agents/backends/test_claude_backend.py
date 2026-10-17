"""ClaudeCodeBackend translates SDK messages into neutral AgentEvents."""
from __future__ import annotations

import asyncio

from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

from lightfall.agents.backend import (
    ContextUsageEvent,
    SessionIdEvent,
    TextBlockEvent,
    TurnResultEvent,
)
from lightfall.agents.backends.claude_code.backend import ClaudeCodeBackend


class _Client:
    def __init__(self, msgs, ctx=None):
        self.msgs, self.ctx = msgs, ctx

    async def query(self, prompt):
        pass

    async def receive_response(self):
        for m in self.msgs:
            yield m

    async def get_context_usage(self):
        return self.ctx


def _result(sid="sess"):
    return ResultMessage(subtype="success", duration_ms=0, duration_api_ms=0,
                         is_error=False, num_turns=1, session_id=sid)


def _collect(backend, prompt="hi"):
    async def go():
        return [e async for e in backend.run_turn(prompt, lambda: False, lambda: False)]
    return asyncio.run(go())


def test_backend_falls_back_to_full_blocks():
    msg = AssistantMessage(content=[TextBlock(text="full text")], model="m")
    events = _collect(ClaudeCodeBackend(_Client([msg, _result()], ctx={"totalTokens": 3})))
    assert events[0] == TextBlockEvent("full text")
    assert isinstance(events[1], TurnResultEvent)
    assert events[1].info["session_id"] == "sess"
    assert events[2] == SessionIdEvent("sess")
    assert events[3] == ContextUsageEvent({"totalTokens": 3})


def test_backend_omits_session_and_ctx_when_absent():
    events = _collect(ClaudeCodeBackend(_Client([_result(sid="")], ctx=None)))
    assert [type(e).__name__ for e in events] == ["TurnResultEvent"]


def test_claude_oneshot_uses_isolated_bypass_options(monkeypatch, tmp_path):
    from lightfall.agents.backends.claude_code import oneshot

    seen = {}

    class _Client:
        def __init__(self, options):
            seen["opts"] = options

        async def connect(self):
            pass

        async def disconnect(self):
            pass

    async def fake_collect(client, prompt):
        return f"echo:{prompt}"

    monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", _Client)
    monkeypatch.setattr("lightfall.monitor.advisor.collect_reply", fake_collect)
    out = oneshot.claude_oneshot("hi", system_prompt="SYS", cwd=str(tmp_path), model="haiku")
    o = seen["opts"]
    assert out == "echo:hi"
    assert (o.system_prompt, o.permission_mode, o.max_turns, o.setting_sources, o.model) == (
        "SYS", "bypassPermissions", 1, [], "haiku",
    )
    assert o.allowed_tools == [] and o.mcp_servers == {}
