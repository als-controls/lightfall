# tests/claude/test_set_model.py
from __future__ import annotations

import asyncio

from claude_agent_sdk.types import ResultMessage
from lightfall.claude._internal.worker import PersistentClaudeWorker


class _ModelStubClient:
    def __init__(self) -> None:
        self.models: list[str] = []
        self._set_evt = asyncio.Event()

    async def connect(self, prompt: str | None = None) -> None:
        pass

    async def query(self, prompt: str) -> None:
        pass

    async def receive_response(self):
        yield ResultMessage(
            subtype="success", duration_ms=0, duration_api_ms=0,
            is_error=False, num_turns=1, session_id="s",
        )

    async def interrupt(self) -> None:
        pass

    async def set_model(self, model: str | None = None) -> None:
        self.models.append(model)
        self._set_evt.set()


def test_request_set_model_calls_client_on_loop(qtbot):
    client = _ModelStubClient()
    worker = PersistentClaudeWorker(client)
    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        worker.request_set_model("haiku")
        qtbot.waitUntil(lambda: client.models == ["haiku"], timeout=3000)
    finally:
        worker.stop()
        assert worker.wait(3000)
