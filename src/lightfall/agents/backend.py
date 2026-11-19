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
class TurnStartedEvent:
    """A turn began in a session stream (session-stream backends only).

    Fired for every turn: ones the host's prompts started and ones the
    session started on its own (e.g. a background-task notification).
    """


@dataclass(frozen=True)
class PromptAcceptedEvent:
    """The session took one of the host's prompts into the conversation.

    Session-stream backends only. A prompt sent while a turn runs is taken
    into that turn (no turn of its own), so the host counts these rather
    than results to know when every prompt it sent has been answered.
    """


@dataclass(frozen=True)
class TurnResultEvent:
    """End of turn. ``info`` keeps the legacy result_received dict shape:
    total_cost_usd, input_tokens, output_tokens, session_id; backends may add
    ``text``, the turn's final assistant text. Session-stream backends also
    set ``origin`` ("" for a turn the host's prompt started, else the kind of
    injected turn, e.g. "task-notification") and ``cancelled`` (the turn was
    interrupted)."""

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
    | TurnStartedEvent | PromptAcceptedEvent
    | TurnResultEvent | SessionIdEvent | ContextUsageEvent | TurnCancelledEvent | TurnErrorEvent
)


class AgentBackend(Protocol):
    """One live agent session. All coroutines run on the AgentWorker's loop.

    Two shapes. A turn-based backend implements ``run_turn``: one prompt in,
    that turn's events out. A session-stream backend sets ``session_stream =
    True`` and implements ``send`` + ``events`` instead (see
    :class:`SessionStreamBackend`), for sessions whose stream also carries
    turns the session starts on its own.
    """

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


class SessionStreamBackend(Protocol):
    """A session whose one output stream carries every turn.

    The stream interleaves turns the host's prompts start with turns the
    session injects (background-task notifications and the like), and a
    prompt sent mid-turn joins the running turn. So prompts and results do
    not pair up; ``events`` reports turn boundaries and prompt acceptance and
    the worker derives busy/idle from them.
    """

    session_stream: bool  # True

    async def connect(self) -> None: ...

    async def send(self, prompt: str) -> None:
        """Hand ``prompt`` to the session; returns without waiting for the turn."""
        ...

    def events(self, stopping: Callable[[], bool]) -> AsyncIterator[AgentEvent]:
        """Yield the session's events for its whole life.

        Every turn opens with TurnStartedEvent and closes with TurnResultEvent
        (optionally followed by SessionIdEvent / ContextUsageEvent). Yields
        PromptAcceptedEvent once per prompt the session takes in. Ends with
        TurnErrorEvent, or by returning, when the session dies; returns when
        ``stopping()`` turns true.
        """
        ...

    async def interrupt(self) -> None: ...

    async def set_model(self, model: str | None) -> None: ...

    async def set_effort(self, effort: str | None) -> None: ...

    async def close(self) -> None: ...


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
