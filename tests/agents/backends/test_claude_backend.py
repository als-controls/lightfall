"""ClaudeCodeBackend translates the SDK session stream into neutral AgentEvents."""
from __future__ import annotations

import asyncio

from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from lightfall.agents.backend import (
    ContextUsageEvent,
    PromptAcceptedEvent,
    SessionIdEvent,
    TaskFinishedEvent,
    TextBlockEvent,
    TurnResultEvent,
    TurnStartedEvent,
)
from lightfall.agents.backends.claude_code.backend import ClaudeCodeBackend


class _Client:
    def __init__(self, msgs, ctx=None):
        self.msgs, self.ctx = msgs, ctx

    async def query(self, prompt):
        pass

    async def receive_messages(self):
        for m in self.msgs:
            yield m

    async def get_context_usage(self):
        return self.ctx


def _result(sid="sess", **kw):
    kw.setdefault("num_turns", 1)
    return ResultMessage(subtype="success", duration_ms=0, duration_api_ms=0,
                         is_error=False, session_id=sid, **kw)


def _all_events(backend):
    async def go():
        return [e async for e in backend.events(lambda: False)]
    return asyncio.run(go())


def _collect(backend):
    """The stream's events minus turn-boundary bookkeeping."""
    return [e for e in _all_events(backend) if not isinstance(e, TurnStartedEvent)]


def test_backend_falls_back_to_full_blocks():
    msg = AssistantMessage(content=[TextBlock(text="full text")], model="m")
    events = _collect(ClaudeCodeBackend(_Client([msg, _result()], ctx={"totalTokens": 3})))
    assert events[0] == TextBlockEvent("full text")
    assert isinstance(events[1], TurnResultEvent)
    assert events[1].info["session_id"] == "sess"
    assert events[2] == SessionIdEvent("sess")
    assert events[3] == ContextUsageEvent({"totalTokens": 3})


def _delta(text):
    return StreamEvent(uuid="u", session_id="s", event={
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": text},
    })


def _result_info(events):
    return next(e for e in events if isinstance(e, TurnResultEvent)).info


def test_result_carries_final_text_when_streamed():
    """Streaming suppresses TextBlockEvent; the full text still reaches the result."""
    msgs = [_delta("Pong"), AssistantMessage(content=[TextBlock(text="Pong")], model="m"),
            _result()]
    events = _collect(ClaudeCodeBackend(_Client(msgs)))
    assert not any(isinstance(e, TextBlockEvent) for e in events)
    assert _result_info(events)["text"] == "Pong"


def test_result_text_is_last_run_after_tool_call():
    msgs = [
        AssistantMessage(content=[TextBlock(text="Let me check.")], model="m"),
        AssistantMessage(content=[ToolUseBlock(id="t1", name="Read", input={})], model="m"),
        AssistantMessage(content=[TextBlock(text="Found it.")], model="m"),
        AssistantMessage(content=[TextBlock(text="All done.")], model="m"),
        _result(),
    ]
    info = _result_info(_collect(ClaudeCodeBackend(_Client(msgs))))
    assert info["text"] == "Found it.\n\nAll done."


def test_result_text_excludes_subagent_messages():
    msgs = [
        AssistantMessage(content=[TextBlock(text="Parent reply.")], model="m"),
        AssistantMessage(content=[TextBlock(text="subagent chatter")], model="m",
                         parent_tool_use_id="task-1"),
        AssistantMessage(content=[ToolUseBlock(id="t2", name="Read", input={})], model="m",
                         parent_tool_use_id="task-1"),
        _result(),
    ]
    info = _result_info(_collect(ClaudeCodeBackend(_Client(msgs))))
    assert info["text"] == "Parent reply."


def test_result_text_empty_without_text_blocks():
    assert _result_info(_collect(ClaudeCodeBackend(_Client([_result()]))))["text"] == ""


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


class _ClosableClient:
    def __init__(self, raises=False):
        self.disconnects = 0
        self.raises = raises

    async def disconnect(self):
        self.disconnects += 1
        if self.raises:
            raise RuntimeError("transport already gone")


def test_close_disconnects_the_sdk_client():
    # An undisconnected client keeps its CLI child and message reader alive;
    # at exit the SDK's atexit hook kills the child and the reader logs
    # "Fatal error in message reader" (GlitchTip LIGHTFALL-5).
    client = _ClosableClient()
    asyncio.run(ClaudeCodeBackend(client).close())
    assert client.disconnects == 1


def test_close_swallows_disconnect_errors():
    client = _ClosableClient(raises=True)
    asyncio.run(ClaudeCodeBackend(client).close())  # must not raise
    assert client.disconnects == 1


# --- turn boundaries, prompt echoes, injected turns -------------------------

def _init():
    return SystemMessage(subtype="init", data={})


def _notification():
    return TaskNotificationMessage(
        subtype="task_notification", data={}, task_id="bg1", status="stopped",
        output_file="", summary="Background shell command didn't finish",
        uuid="u-n", session_id="sess",
    )


def _names(events):
    return [type(e).__name__ for e in events]


def test_injected_turn_before_prompt_is_its_own_turn():
    """The 2026-11-19 sequence: a task notification pending at send time makes
    the CLI run (and close) an injected zero-turn turn before the prompt's."""
    msgs = [
        _notification(),
        _init(),
        _result(num_turns=0, origin={"kind": "task-notification"}),
        _init(),
        UserMessage(content="I restarted. Are we able to test rixs-live?"),
        AssistantMessage(content=[TextBlock(text="Checking.")], model="m"),
        _result(num_turns=14, terminal_reason="completed"),
    ]
    events = _all_events(ClaudeCodeBackend(_Client(msgs)))
    assert _names(events) == [
        "TaskFinishedEvent",
        "TurnStartedEvent", "TurnResultEvent", "SessionIdEvent",
        "TurnStartedEvent", "PromptAcceptedEvent", "TextBlockEvent",
        "TurnResultEvent", "SessionIdEvent",
    ]
    results = [e.info for e in events if isinstance(e, TurnResultEvent)]
    assert results[0]["origin"] == "task-notification"
    assert results[1]["origin"] == ""
    assert results[1]["text"] == "Checking."
    assert not any(r["cancelled"] for r in results)


def test_prompt_sent_mid_turn_is_accepted_into_the_running_turn():
    msgs = [
        _init(),
        UserMessage(content="A"),
        AssistantMessage(content=[ToolUseBlock(id="t1", name="Bash", input={})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok")]),
        UserMessage(content="B"),
        AssistantMessage(content=[TextBlock(text="B-DONE")], model="m"),
        _result(num_turns=2),
    ]
    events = _all_events(ClaudeCodeBackend(_Client(msgs)))
    assert _names(events).count("TurnStartedEvent") == 1
    assert _names(events).count("PromptAcceptedEvent") == 2
    assert _names(events).count("TurnResultEvent") == 1


def test_only_plain_string_user_messages_count_as_prompt_echoes():
    msgs = [
        _init(),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok")]),
        # The CLI's "[Request interrupted by user]" marker is a block list.
        UserMessage(content=[TextBlock(text="[Request interrupted by user]")]),
        UserMessage(content="subagent prompt", parent_tool_use_id="task-1"),
        UserMessage(content="<task-notification>", origin={"kind": "task-notification"}),
        UserMessage(content="typed by a human", origin={"kind": "human"}),
        _result(),
    ]
    events = _all_events(ClaudeCodeBackend(_Client(msgs)))
    assert sum(isinstance(e, PromptAcceptedEvent) for e in events) == 1


def test_task_messages_between_turns_do_not_open_a_turn():
    msgs = [_result(), _notification()]
    events = _all_events(ClaudeCodeBackend(_Client(msgs)))
    assert _names(events)[-1] == "TaskFinishedEvent"
    assert _names(events).count("TurnStartedEvent") == 1  # the result's own turn
    assert isinstance(events[-1], TaskFinishedEvent)


def test_interrupted_turn_result_is_marked_cancelled():
    msgs = [_init(), UserMessage(content="go"),
            ResultMessage(subtype="error_during_execution", duration_ms=0,
                          duration_api_ms=0, is_error=False, num_turns=2,
                          session_id="s", terminal_reason="aborted_streaming")]
    info = _result_info(_collect(ClaudeCodeBackend(_Client(msgs))))
    assert info["cancelled"] is True


def test_turn_state_resets_between_turns():
    """Streamed text in one turn must not suppress the fallback emit in the next."""
    msgs = [
        _init(), _delta("Pong"),
        AssistantMessage(content=[TextBlock(text="Pong")], model="m"), _result(),
        _init(), AssistantMessage(content=[TextBlock(text="unstreamed")], model="m"),
        _result(),
    ]
    events = _collect(ClaudeCodeBackend(_Client(msgs)))
    assert TextBlockEvent("unstreamed") in events
    assert TextBlockEvent("Pong") not in events
    results = [e.info for e in events if isinstance(e, TurnResultEvent)]
    assert [r["text"] for r in results] == ["Pong", "unstreamed"]


def test_send_writes_the_prompt():
    sent = []

    class _C(_Client):
        async def query(self, prompt):
            sent.append(prompt)

    asyncio.run(ClaudeCodeBackend(_C([])).send("hello"))
    assert sent == ["hello"]
