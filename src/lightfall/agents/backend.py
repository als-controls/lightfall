"""Backend-neutral agent contract: turn events and the AgentBackend protocol."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class TextBlockEvent:
    text: str


@dataclass(frozen=True)
class ThinkingBlockEvent:
    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResultEvent:
    tool_use_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class PartialStartedEvent:
    block_id: str
    kind: str


@dataclass(frozen=True)
class PartialTextEvent:
    block_id: str
    delta: str


@dataclass(frozen=True)
class PartialThinkingEvent:
    block_id: str
    delta: str


@dataclass(frozen=True)
class PartialFinishedEvent:
    block_id: str


@dataclass(frozen=True)
class TaskStartedEvent:
    task_id: str
    description: str
    tool_use_id: str


@dataclass(frozen=True)
class TaskProgressEvent:
    task_id: str
    description: str
    usage: dict[str, Any]
    last_tool: str


@dataclass(frozen=True)
class TaskFinishedEvent:
    task_id: str
    status: str
    summary: str
    output_file: str
    usage: dict[str, Any]


@dataclass(frozen=True)
class TurnResultEvent:
    """End of turn. ``info`` keeps the legacy result_received dict shape:
    total_cost_usd, input_tokens, output_tokens, session_id."""

    info: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionIdEvent:
    session_id: str


@dataclass(frozen=True)
class ContextUsageEvent:
    usage: dict[str, Any]


@dataclass(frozen=True)
class TurnCancelledEvent:
    pass


@dataclass(frozen=True)
class TurnErrorEvent:
    message: str


AgentEvent = (
    TextBlockEvent | ThinkingBlockEvent | ToolCallEvent | ToolResultEvent
    | PartialStartedEvent | PartialTextEvent | PartialThinkingEvent | PartialFinishedEvent
    | TaskStartedEvent | TaskProgressEvent | TaskFinishedEvent
    | TurnResultEvent | SessionIdEvent | ContextUsageEvent | TurnCancelledEvent | TurnErrorEvent
)


class AgentBackend(Protocol):
    """One live agent session. All coroutines run on the AgentWorker's loop."""

    async def connect(self) -> None: ...

    def run_turn(
        self,
        prompt: str,
        cancelled: Callable[[], bool],
        stopping: Callable[[], bool],
    ) -> AsyncIterator[AgentEvent]:
        """Send ``prompt`` and yield events until the turn ends.

        Must end with exactly one of TurnResultEvent (optionally followed by
        SessionIdEvent / ContextUsageEvent), TurnCancelledEvent, or
        TurnErrorEvent — or simply stop when ``stopping()`` turns true.
        """
        ...

    async def interrupt(self) -> None: ...

    async def set_model(self, model: str | None) -> None: ...
