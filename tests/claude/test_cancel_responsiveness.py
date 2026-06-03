"""PersistentClaudeWorker.cancel_current_query() must actually preempt the
streaming receive loop, not just flip a flag and hope a message lands.

The pre-fix worker would set ``_cancel_requested = True`` and then wait —
``async for msg in client.receive_response()`` blocks on subprocess stdout, so
the flag is not consulted until the CLI emits the next message. While the
model is generating or running a long tool, that can be many seconds. We use
the SDK's ``client.interrupt()`` control message to tell the CLI to stop the
turn immediately, which unblocks the read.

These tests would hang (then time out) against the old behavior.
"""
from __future__ import annotations

import asyncio

import pytest

from claude_agent_sdk.types import ResultMessage
from lightfall.claude._internal.worker import PersistentClaudeWorker


class _StubClient:
    """Minimal ClaudeSDKClient stub.

    ``receive_response`` blocks until ``interrupt`` is called, then yields a
    ``ResultMessage`` so the worker's outer ``async for`` can complete its
    cancel check. This mirrors how the real CLI behaves when an interrupt
    control message arrives mid-turn.
    """

    def __init__(self) -> None:
        self.interrupt_calls = 0
        self.query_calls: list[str] = []
        # Created lazily on the worker's event loop so the Event binds there.
        self._interrupted: asyncio.Event | None = None

    async def connect(self, prompt: str | None = None) -> None:
        self._interrupted = asyncio.Event()

    async def query(self, prompt: str) -> None:
        self.query_calls.append(prompt)

    async def receive_response(self):
        assert self._interrupted is not None, "connect() not awaited"
        await self._interrupted.wait()
        yield ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="test",
            stop_reason="interrupted",
            total_cost_usd=0.0,
            usage={},
        )

    async def interrupt(self) -> None:
        self.interrupt_calls += 1
        assert self._interrupted is not None
        self._interrupted.set()


@pytest.fixture
def stub_client() -> _StubClient:
    return _StubClient()


def test_cancel_invokes_sdk_interrupt(qtbot, stub_client: _StubClient) -> None:
    """cancel_current_query() must dispatch client.interrupt() on the worker
    loop so the CLI receives the control signal — otherwise cancel is a
    no-op until the next stream message lands on its own."""
    worker = PersistentClaudeWorker(stub_client)
    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)

        with qtbot.waitSignal(worker.query_cancelled, timeout=3000):
            worker.send_query("hello")
            qtbot.waitUntil(lambda: worker.is_processing, timeout=2000)
            assert worker.cancel_current_query() is True

        assert stub_client.interrupt_calls >= 1, (
            "Expected client.interrupt() to be called on cancel. Without it, "
            "the worker stays blocked in receive_response() until the CLI "
            "emits the next message — the symptom we are fixing."
        )
    finally:
        worker.stop()
        assert worker.wait(3000), "worker did not stop within 3s"


def test_cancel_when_not_processing_does_not_interrupt(
    qtbot, stub_client: _StubClient
) -> None:
    """If no query is running, cancel returns False and does NOT fire a
    spurious interrupt at the CLI."""
    worker = PersistentClaudeWorker(stub_client)
    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)

        assert worker.cancel_current_query() is False
        assert stub_client.interrupt_calls == 0
    finally:
        worker.stop()
        assert worker.wait(3000)
