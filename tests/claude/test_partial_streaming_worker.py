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
