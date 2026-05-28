"""PersistentClaudeWorker dispatches StreamEvent into partial_* signals."""
from __future__ import annotations

import asyncio

import pytest

from claude_agent_sdk.types import ResultMessage, StreamEvent
from lucid.claude._internal.worker import PersistentClaudeWorker


class _StreamStubClient:
    """Stub client that yields StreamEvents then ResultMessage."""

    def __init__(self) -> None:
        self._events: list = []

    def script(self, events: list) -> None:
        self._events = list(events)

    async def connect(self, prompt: str | None = None) -> None:
        pass

    async def query(self, prompt: str) -> None:
        pass

    async def receive_response(self):
        for e in self._events:
            yield e

    async def interrupt(self) -> None:
        pass


def _make_stream_event(event_dict: dict, *, uuid: str = "msg-1") -> StreamEvent:
    return StreamEvent(
        uuid=uuid, session_id="sess", event=event_dict, parent_tool_use_id=None
    )


def _result() -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=0, duration_api_ms=0,
        is_error=False, num_turns=1, session_id="sess",
    )


def test_text_streaming_emits_started_text_finished(qtbot):
    client = _StreamStubClient()
    client.script([
        _make_stream_event({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        }),
        _make_stream_event({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Hello "},
        }),
        _make_stream_event({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "world"},
        }),
        _make_stream_event({"type": "content_block_stop", "index": 0}),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    started: list[tuple[str, str]] = []
    texts: list[tuple[str, str]] = []
    finished: list[str] = []
    worker.partial_block_started.connect(
        lambda bid, kind: started.append((bid, kind))
    )
    worker.partial_text.connect(lambda bid, d: texts.append((bid, d)))
    worker.partial_block_finished.connect(finished.append)

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        assert started == [("msg-1:0", "text")]
        assert texts == [("msg-1:0", "Hello "), ("msg-1:0", "world")]
        assert finished == ["msg-1:0"]
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_thinking_streaming_emits_partial_thinking(qtbot):
    client = _StreamStubClient()
    client.script([
        _make_stream_event({
            "type": "content_block_start", "index": 1,
            "content_block": {"type": "thinking"},
        }),
        _make_stream_event({
            "type": "content_block_delta", "index": 1,
            "delta": {"type": "thinking_delta", "thinking": "Let me think…"},
        }),
        _make_stream_event({"type": "content_block_stop", "index": 1}),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    thinks: list[tuple[str, str]] = []
    worker.partial_thinking.connect(lambda bid, d: thinks.append((bid, d)))

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        assert thinks == [("msg-1:1", "Let me think…")]
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_assistant_message_always_emits_text_in_addition_to_streaming(qtbot):
    """The worker now always emits message_received for AssistantMessage's
    TextBlock, even when streaming covered the same text. Dedup is the
    widget's job — the worker can't predict what the widget actually
    rendered, and predicting wrong leaves blank cards (the production
    failure mode that motivated this change)."""
    from claude_agent_sdk.types import AssistantMessage, TextBlock

    client = _StreamStubClient()
    client.script([
        _make_stream_event({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text"},
        }),
        _make_stream_event({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        }),
        _make_stream_event({"type": "content_block_stop", "index": 0}),
        AssistantMessage(content=[TextBlock(text="Hello")], model="claude-sonnet-4-6", parent_tool_use_id=None),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    msg_received: list[str] = []
    worker.message_received.connect(msg_received.append)
    texts: list[tuple[str, str]] = []
    worker.partial_text.connect(lambda b, d: texts.append((b, d)))

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        # Streaming did its thing AND AssistantMessage emitted as the
        # canonical fallback that the widget can trust.
        assert texts == [("msg-1:0", "Hello")]
        assert msg_received == ["Hello"]
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_assistant_message_emits_when_no_stream_events_fired(qtbot):
    """Defense-in-depth: if no partial events arrived for this query (e.g.
    the CLI didn't honor --include-partial-messages, or the SDK shape
    changed and our dispatch didn't match), fall back to emitting from
    the AssistantMessage so the chat doesn't go blank.

    Pre-fix this test FAILS — the suppression was unconditional, so
    when zero stream events fired, the assembled AssistantMessage's
    TextBlock was also suppressed and the user saw an empty card.
    """
    from claude_agent_sdk.types import AssistantMessage, TextBlock

    client = _StreamStubClient()
    # No StreamEvents at all — just the assembled AssistantMessage.
    client.script([
        AssistantMessage(
            content=[TextBlock(text="hello fallback")],
            model="claude-sonnet-4-6", parent_tool_use_id=None,
        ),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    msg_received: list[str] = []
    worker.message_received.connect(msg_received.append)

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        # Streaming silently didn't happen — AssistantMessage must emit so
        # the widget renders SOMETHING instead of a blank card.
        assert msg_received == ["hello fallback"]
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_thinking_fallback_when_no_stream_events_fired(qtbot):
    """Same defense for thinking blocks."""
    from claude_agent_sdk.types import AssistantMessage, ThinkingBlock

    client = _StreamStubClient()
    client.script([
        AssistantMessage(
            content=[ThinkingBlock(thinking="pondering…", signature="sig")],
            model="claude-sonnet-4-6", parent_tool_use_id=None,
        ),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    thinks: list[str] = []
    worker.thinking_received.connect(thinks.append)

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        assert thinks == ["pondering…"]
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_initial_thinking_text_in_block_start_is_emitted(qtbot):
    """thinking={display=summarized} ships the whole summary as the
    initial ``thinking`` field of content_block_start, with no deltas
    following. The dispatcher must emit it via partial_thinking so the
    bubble doesn't stay empty."""
    client = _StreamStubClient()
    client.script([
        _make_stream_event({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking",
                              "thinking": "the whole summary in one shot"},
        }),
        _make_stream_event({"type": "content_block_stop", "index": 0}),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    thinks: list[tuple[str, str]] = []
    worker.partial_thinking.connect(lambda bid, d: thinks.append((bid, d)))

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        assert thinks == [("msg-1:0", "the whole summary in one shot")]
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_initial_text_in_block_start_is_emitted(qtbot):
    """If a content_block_start ever ships non-empty initial text (e.g.
    the CLI batches), emit it via partial_text immediately."""
    client = _StreamStubClient()
    client.script([
        _make_stream_event({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": "initial chunk"},
        }),
        _make_stream_event({"type": "content_block_stop", "index": 0}),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    texts: list[tuple[str, str]] = []
    worker.partial_text.connect(lambda bid, d: texts.append((bid, d)))

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        assert texts == [("msg-1:0", "initial chunk")]
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_block_start_without_content_or_deltas_falls_back_to_AssistantMessage(qtbot):
    """The exact production failure: content_block_start fires for thinking
    (so partial_block_started creates a bubble), but no delta follows AND
    no initial content was shipped. Without this protection the suppression
    fires (because a block started) and the AssistantMessage's ThinkingBlock
    is swallowed — user sees a blank thinking card.

    With the fix, _saw_partial_events is only set when actual content is
    emitted (deltas OR initial content in start), so the AssistantMessage
    fallback fires and the chat shows something.
    """
    from claude_agent_sdk.types import AssistantMessage, ThinkingBlock

    client = _StreamStubClient()
    client.script([
        _make_stream_event({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking"},  # NO initial content
        }),
        # NO content_block_delta
        _make_stream_event({"type": "content_block_stop", "index": 0}),
        AssistantMessage(
            content=[ThinkingBlock(thinking="full summary text", signature="s")],
            model="claude-sonnet-4-6", parent_tool_use_id=None,
        ),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    thinks: list[str] = []
    worker.thinking_received.connect(thinks.append)

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        assert thinks == ["full summary text"]
    finally:
        worker.stop()
        assert worker.wait(3000)
