# tests/claude/test_session_capture.py
from __future__ import annotations

from claude_agent_sdk.types import ResultMessage
from lightfall.claude._internal.worker import PersistentClaudeWorker


class _SidStubClient:
    async def connect(self, prompt: str | None = None) -> None:
        pass

    async def query(self, prompt: str) -> None:
        pass

    async def receive_response(self):
        yield ResultMessage(
            subtype="success", duration_ms=0, duration_api_ms=0,
            is_error=False, num_turns=1, session_id="sess-XYZ",
            total_cost_usd=0.01, usage={"input_tokens": 1, "output_tokens": 1},
        )

    async def interrupt(self) -> None:
        pass

    async def get_context_usage(self):
        return {}


def test_worker_emits_session_id(qtbot):
    client = _SidStubClient()
    worker = PersistentClaudeWorker(client)
    sids: list[str] = []
    worker.session_id_changed.connect(sids.append)
    payloads: list[dict] = []
    worker.result_received.connect(payloads.append)

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.session_id_changed, timeout=3000):
            worker.send_query("hi")
        assert sids == ["sess-XYZ"]
        assert payloads[0]["session_id"] == "sess-XYZ"
    finally:
        worker.stop()
        assert worker.wait(3000)
