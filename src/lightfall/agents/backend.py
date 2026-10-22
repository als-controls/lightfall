"""Backend-neutral agent contract: turn events and the AgentBackend protocol."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from lightfall.agents.tooling import ToolServer


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
    total_cost_usd, input_tokens, output_tokens, session_id; backends may add
    ``text``, the turn's final assistant text."""

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

    async def set_effort(self, effort: str | None) -> None:
        """Reasoning effort for the next turn. Only called when ``caps.live_set_effort``."""
        ...

    async def close(self) -> None:
        """Release backend resources (child processes, servers).

        Called once when the worker stops; must not raise.
        """
        ...


@dataclass(frozen=True)
class SubagentSpec:
    name: str
    description: str
    prompt: str
    model: str | None = None
    effort: str | None = None
    memory: bool | None = None


@dataclass
class SessionConfig:
    """Everything a backend needs to open one session (backend-neutral)."""

    cwd: str
    system_prompt: str
    tool_servers: list[tuple[str, ToolServer]]  # (session key, server)
    external_servers: dict[str, Any]
    allowed_tools: list[str]
    subagents: list[SubagentSpec]
    skills_dir: Path
    permission_mode: str = "default"
    max_turns: int = 20
    model: str | None = None
    effort: str | None = None
    resume: str | None = None
    cli_path: str | None = None


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    title: str


@dataclass(frozen=True)
class TranscriptEntry:
    role: str  # "user" | "assistant"
    text: str
    tool_names: list[str]


class SessionStore(Protocol):
    def list_sessions(self, limit: int = 15) -> list[SessionInfo]: ...

    def load_transcript(self, session_id: str) -> list[TranscriptEntry]: ...
