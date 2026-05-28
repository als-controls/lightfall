"""PersistentClaudeWorker parses Task* messages from the stream."""
from __future__ import annotations

import pytest

from claude_agent_sdk.types import (
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
)
from lucid.claude._internal.worker import PersistentClaudeWorker


class _TaskStubClient:
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


def _result() -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=0, duration_api_ms=0,
        is_error=False, num_turns=1, session_id="sess",
    )


def test_task_lifecycle_signals(qtbot):
    client = _TaskStubClient()
    started_msg = TaskStartedMessage(
        subtype="task_started", data={},
        task_id="t1", description="investigate", uuid="u1",
        session_id="sess", tool_use_id="tool-use-42",
    )
    progress_msg = TaskProgressMessage(
        subtype="task_progress", data={},
        task_id="t1", description="investigate",
        usage={"total_tokens": 1500, "tool_uses": 3, "duration_ms": 100},
        uuid="u2", session_id="sess",
        last_tool_name="Read",
    )
    finished_msg = TaskNotificationMessage(
        subtype="task_notification", data={},
        task_id="t1", status="completed",
        output_file="/tmp/x.jsonl",
        summary="Found three widgets",
        uuid="u3", session_id="sess",
        usage={"total_tokens": 2000, "tool_uses": 5, "duration_ms": 250},
    )
    client.script([started_msg, progress_msg, finished_msg, _result()])

    worker = PersistentClaudeWorker(client)
    started: list[tuple[str, str, str]] = []
    progress: list[tuple[str, str, dict, str]] = []
    finished: list[tuple[str, str, str, str, dict]] = []
    worker.task_started.connect(
        lambda a, b, c: started.append((a, b, c))
    )
    worker.task_progress.connect(
        lambda a, b, c, d: progress.append((a, b, dict(c), d))
    )
    worker.task_finished.connect(
        lambda a, b, c, d, e: finished.append((a, b, c, d, dict(e)))
    )

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("go")

        assert started == [("t1", "investigate", "tool-use-42")]
        assert progress == [(
            "t1", "investigate",
            {"total_tokens": 1500, "tool_uses": 3, "duration_ms": 100},
            "Read",
        )]
        assert finished == [(
            "t1", "completed", "Found three widgets", "/tmp/x.jsonl",
            {"total_tokens": 2000, "tool_uses": 5, "duration_ms": 250},
        )]
    finally:
        worker.stop()
        assert worker.wait(3000)
