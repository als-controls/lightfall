from __future__ import annotations

from claude_agent_sdk.types import ResultMessage
from lightfall.claude._internal.worker import PersistentClaudeWorker


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
