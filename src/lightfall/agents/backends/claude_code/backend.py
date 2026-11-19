"""ClaudeCodeBackend: the claude_agent_sdk session stream behind the AgentBackend seam.

Moved from ``lightfall.claude._internal.worker.PersistentClaudeWorker``: this
half talks to the SDK client and translates its stream into neutral
:mod:`lightfall.agents.backend` events; ``AgentWorker`` owns the Qt thread,
loop, flags and signals.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from lightfall.agents.backend import (
    AgentEvent,
    ContextUsageEvent,
    PartialFinishedEvent,
    PartialStartedEvent,
    PartialTextEvent,
    PartialThinkingEvent,
    PromptAcceptedEvent,
    SessionIdEvent,
    TaskFinishedEvent,
    TaskProgressEvent,
    TaskStartedEvent,
    TextBlockEvent,
    ThinkingBlockEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnErrorEvent,
    TurnResultEvent,
    TurnStartedEvent,
)
from lightfall.utils.logging import logger


def _summarize_sdk_msg(msg: Any) -> dict[str, Any]:
    """Diagnostic one-line summary of a Claude Agent SDK stream message.

    Used to investigate silent turn-ending bugs: we need to see every
    ``stop_reason``, ``subtype``, and the shape of content blocks so we
    can tell whether the loop exited on ``end_turn``, ``pause_turn``, or
    a malformed tool result. Grep logs for ``[sdk-stream]`` to filter.
    """
    info: dict[str, Any] = {"type": type(msg).__name__}
    for attr in ("stop_reason", "subtype", "session_id", "is_error", "num_turns"):
        if hasattr(msg, attr):
            info[attr] = getattr(msg, attr)
    content = getattr(msg, "content", None)
    if content is not None:
        if isinstance(content, list):
            info["block_types"] = [type(b).__name__ for b in content]
            info["n_blocks"] = len(content)
        else:
            info["content_type"] = type(content).__name__
    return info


def _is_prompt_echo(msg: Any) -> bool:
    """Is ``msg`` the CLI's replay of a prompt the host sent?

    Host prompts go out as plain strings and come back that way. Tool results,
    the CLI's own "[Request interrupted by user]" marker (a TextBlock list) and
    injected messages (``origin`` set to a non-human kind) are not echoes.
    """
    if getattr(msg, "parent_tool_use_id", None) is not None:
        return False
    origin = getattr(msg, "origin", None)
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return False
    return isinstance(getattr(msg, "content", None), str)


class ClaudeCodeBackend:
    """AgentBackend driving a ``ClaudeSDKClient``."""

    def __init__(self, client: Any, initial_prompt: str | None = None):
        """
        Args:
            client: ClaudeSDKClient instance (not yet connected)
            initial_prompt: Optional initial prompt to send on connection
        """
        self.client = client
        self.initial_prompt = initial_prompt
        self._in_turn = False
        self._begin_turn_state()

    async def connect(self) -> None:
        """Connect to Claude Agent SDK."""
        await self.client.connect(prompt=self.initial_prompt)

    async def interrupt(self) -> None:
        await self.client.interrupt()

    async def set_model(self, model: str | None) -> None:
        await self.client.set_model(model)

    async def set_effort(self, effort: str | None) -> None:
        return None  # read at construction only; callers rebuild the agent

    async def close(self) -> None:
        """Disconnect the SDK client: cancel its message reader, then end the CLI.

        Left connected, the CLI child outlives the worker; at interpreter exit
        the SDK's atexit hook kills it and the still-running reader logs
        "Fatal error in message reader" (GlitchTip LIGHTFALL-5).
        """
        try:
            await self.client.disconnect()
        except Exception as exc:  # noqa: BLE001 - close must not raise
            logger.warning("Claude client disconnect failed: {}", exc)

    # The worker drives send()/events() instead of run_turn(): the CLI's one
    # stream interleaves our turns with ones it injects (background-task
    # notifications, ...), so turns must be read for the session's whole life.
    session_stream = True

    async def send(self, prompt: str) -> None:
        """Write ``prompt`` to the CLI. The CLI queues it if a turn is running."""
        logger.info("[sdk-stream] prompt sent prompt_prefix={!r}", prompt[:80])
        await self.client.query(prompt)

    async def events(self, stopping: Callable[[], bool]) -> AsyncIterator[AgentEvent]:
        """Translate the CLI's message stream into neutral events until it ends.

        Args:
            stopping: True once the worker is shutting down
        """
        self._in_turn = False
        self._begin_turn_state()
        try:
            async for msg in self.client.receive_messages():
                logger.info("[sdk-stream] msg {}", _summarize_sdk_msg(msg))
                if stopping():
                    return
                async for ev in self._handle(msg):
                    yield ev
            logger.warning("[sdk-stream] CLI message stream ended")
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            error_msg = str(e)
            logger.exception("[sdk-stream] session stream error")
            # Detect CLI process death (common on Windows)
            if "stream closed" in error_msg.lower() or "0xC0000005" in error_msg:
                yield TurnErrorEvent(
                    "Claude CLI process terminated unexpectedly. "
                    "This may be a transient issue — try sending another message."
                )
            else:
                yield TurnErrorEvent(f"Agent stream error: {error_msg}\n{error_details}")

    def _begin_turn_state(self) -> None:
        # Track whether the CLI actually streamed text/thinking blocks for
        # this turn. The AssistantMessage handler uses this to decide
        # whether to suppress its TextBlock/ThinkingBlock emit (avoiding
        # double-render) or fall back to emitting (so the chat doesn't go
        # blank if streaming silently failed for any reason).
        self._saw_partial_events = False
        # The Anthropic streaming protocol identifies a content block by
        # ``(message.id, index)``. ``StreamEvent.uuid`` is a per-event ID
        # — NOT the per-message ID. We track the current message_id
        # from ``message_start`` events and use it to build block_ids so
        # that content_block_start / content_block_delta / content_block_stop
        # events for the same block correlate.
        self._current_message_id = ""
        # The turn's final assistant text, carried on TurnResultEvent.info
        # ["text"] for consumers that don't render partial_text (voice
        # summaries). Streaming suppresses TextBlockEvent, so this is the
        # only place the complete text survives. Keep the last contiguous
        # run of top-level text: a tool call starts a new run, and subagent
        # messages (parent_tool_use_id set) never count.
        self._final_text: list[str] = []
        self._tool_since_text = False

    def _start_turn(self) -> Iterator[AgentEvent]:
        """Open a turn unless one is open (yields TurnStartedEvent once)."""
        if not self._in_turn:
            self._in_turn = True
            self._begin_turn_state()
            yield TurnStartedEvent()

    async def _handle(self, msg: Any) -> AsyncIterator[AgentEvent]:
        """Translate one SDK message."""
        from claude_agent_sdk.types import (
            AssistantMessage,
            ResultMessage,
            StreamEvent,
            SystemMessage,
            TaskNotificationMessage,
            TaskProgressMessage,
            TaskStartedMessage,
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )

        # Task*Message subclass SystemMessage: test them first. They arrive
        # between turns too (a background task finishing while idle), so they
        # never open a turn.
        if isinstance(msg, TaskStartedMessage):
            yield TaskStartedEvent(msg.task_id, msg.description, msg.tool_use_id or "")
        elif isinstance(msg, TaskProgressMessage):
            yield TaskProgressEvent(
                msg.task_id, msg.description,
                dict(msg.usage) if msg.usage else {},
                msg.last_tool_name or "",
            )
        elif isinstance(msg, TaskNotificationMessage):
            yield TaskFinishedEvent(
                msg.task_id, msg.status, msg.summary or "",
                msg.output_file or "",
                dict(msg.usage) if msg.usage else {},
            )

        elif isinstance(msg, SystemMessage):
            # The CLI opens every turn, ours or injected, with an init message.
            if msg.subtype == "init":
                for ev in self._start_turn():
                    yield ev

        elif isinstance(msg, UserMessage):
            for ev in self._start_turn():
                yield ev
            if _is_prompt_echo(msg):
                # --replay-user-messages echo: one of our prompts joined the
                # conversation (as its own turn, or mid-turn into a running one).
                logger.info("[sdk-stream] prompt accepted")
                yield PromptAcceptedEvent()

        elif isinstance(msg, AssistantMessage):
            for ev in self._start_turn():
                yield ev
            top_level = getattr(msg, "parent_tool_use_id", None) is None
            for block in msg.content:
                if isinstance(block, TextBlock):
                    if top_level and block.text:
                        if self._tool_since_text:
                            self._final_text.clear()
                            self._tool_since_text = False
                        self._final_text.append(block.text)
                    # If we streamed content for this turn, the
                    # widget already rendered it via partial_text.
                    # Otherwise emit so the chat doesn't go blank.
                    if self._saw_partial_events:
                        logger.info(
                            "[sdk-stream] TextBlock len={} (streamed; skip)",
                            len(block.text or ""),
                        )
                    else:
                        logger.info(
                            "[sdk-stream] TextBlock len={} (no stream; emit)",
                            len(block.text or ""),
                        )
                        yield TextBlockEvent(block.text)
                elif isinstance(block, ThinkingBlock):
                    thinking_text = getattr(block, "thinking", "") or ""
                    if self._saw_partial_events:
                        logger.info(
                            "[sdk-stream] ThinkingBlock len={} (streamed; skip)",
                            len(thinking_text),
                        )
                    else:
                        logger.info(
                            "[sdk-stream] ThinkingBlock len={} (no stream; emit)",
                            len(thinking_text),
                        )
                        yield ThinkingBlockEvent(thinking_text)
                elif isinstance(block, ToolUseBlock):
                    if top_level:
                        self._tool_since_text = True
                    logger.info(
                        "[sdk-stream] ToolUseBlock name={} id={}",
                        block.name,
                        getattr(block, "id", None),
                    )
                    yield ToolCallEvent(block.name, block.input)
                elif isinstance(block, ToolResultBlock):
                    content_repr = repr(block.content)
                    logger.info(
                        "[sdk-stream] ToolResultBlock id={} is_error={} "
                        "content_len={} content_preview={!s:.300}",
                        getattr(block, "tool_use_id", None),
                        block.is_error,
                        len(content_repr),
                        content_repr,
                    )
                    yield ToolResultEvent(
                        block.tool_use_id,
                        {"content": block.content, "is_error": block.is_error}
                    )
                else:
                    logger.info(
                        "[sdk-stream] unknown block type={}",
                        type(block).__name__,
                    )

        elif isinstance(msg, StreamEvent):
            for ev in self._start_turn():
                yield ev
            for ev in self._dispatch_stream_event(msg):
                yield ev

        elif isinstance(msg, ResultMessage):
            for ev in self._start_turn():  # a result always closes some turn
                yield ev
            self._in_turn = False
            origin = getattr(msg, "origin", None) or {}
            origin_kind = origin.get("kind", "") if isinstance(origin, dict) else ""
            if origin_kind == "human":
                origin_kind = ""
            terminal_reason = getattr(msg, "terminal_reason", None) or ""
            logger.info(
                "[sdk-stream] ResultMessage stop_reason={} subtype={} origin={!r} "
                "terminal_reason={!r} -> turn ends",
                getattr(msg, "stop_reason", None),
                getattr(msg, "subtype", None),
                origin_kind,
                terminal_reason,
            )
            usage = msg.usage or {}
            yield TurnResultEvent({
                "total_cost_usd": msg.total_cost_usd if hasattr(msg, 'total_cost_usd') else 0,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                "duration_ms": getattr(msg, "duration_ms", 0) or 0,
                "duration_api_ms": getattr(msg, "duration_api_ms", 0) or 0,
                "num_turns": getattr(msg, "num_turns", 0) or 0,
                "is_error": bool(getattr(msg, "is_error", False)),
                "session_id": getattr(msg, "session_id", "") or "",
                "text": "\n\n".join(self._final_text),
                "origin": origin_kind,
                "cancelled": terminal_reason.startswith("aborted"),
            })
            sid = getattr(msg, "session_id", "") or ""
            if sid:
                yield SessionIdEvent(sid)
            # Refresh context-window usage for the cockpit. Best-effort:
            # the SDK routes the control response on its own reader task, so
            # awaiting here only delays (buffers) the stream. Bounded so a
            # wedged CLI can't stall it; never let it break the session.
            try:
                ctx = await asyncio.wait_for(self.client.get_context_usage(), timeout=2.0)
                if ctx:
                    yield ContextUsageEvent(dict(ctx))
            except Exception as exc:  # noqa: BLE001 - best-effort telemetry
                logger.debug("[sdk-stream] get_context_usage failed: {}", exc)
        else:
            logger.info(
                "[sdk-stream] unhandled msg type={} — loop continues",
                type(msg).__name__,
            )

    def _dispatch_stream_event(self, msg: Any) -> Iterator[AgentEvent]:
        """Parse a StreamEvent and yield per-block Partial*Events.

        Block identity is ``{message_id}:{index}`` where ``message_id`` is
        the ``message.id`` field carried by the ``message_start`` event.
        We do NOT use ``StreamEvent.uuid`` — that's a per-event UUID,
        unique to each stream event, so content_block_start /
        content_block_delta / content_block_stop events for the same
        block would all have different uuids and never correlate.

        ``self._saw_partial_events`` is set only when actual content is
        emitted (a non-empty initial content block OR a non-empty delta),
        NOT merely when a ``content_block_start`` fires. That way, a CLI
        that opens a block but never delivers content (e.g. summarized-
        thinking mode batching everything into a single field we don't
        recognize) leaves the flag False, letting the AssistantMessage
        handler fall back to emitting the canonical text.
        """
        event = getattr(msg, "event", None) or {}
        event_type = event.get("type", "")

        # Track the active message_id so subsequent content_block_*
        # events for that message share a block_id prefix.
        if event_type == "message_start":
            message = event.get("message", {}) or {}
            self._current_message_id = message.get("id", "") or ""
            return

        index = event.get("index")
        if index is None:
            return
        block_id = f"{self._current_message_id}:{index}"

        if event_type == "content_block_start":
            block = event.get("content_block", {}) or {}
            kind = block.get("type", "")
            if kind not in ("text", "thinking"):
                return
            yield PartialStartedEvent(block_id, kind)
            # Some CLI modes (e.g. thinking={display:summarized}) ship the
            # whole block content in the start event with no following
            # deltas. Treat that as a single big delta.
            initial = (
                block.get("text", "") if kind == "text"
                else block.get("thinking", "")
            ) or ""
            if initial:
                if kind == "text":
                    yield PartialTextEvent(block_id, initial)
                else:
                    yield PartialThinkingEvent(block_id, initial)
                self._saw_partial_events = True
        elif event_type == "content_block_delta":
            delta = event.get("delta", {}) or {}
            dtype = delta.get("type", "")
            if dtype == "text_delta":
                text = delta.get("text", "") or ""
                if text:
                    yield PartialTextEvent(block_id, text)
                    self._saw_partial_events = True
            elif dtype == "thinking_delta":
                thinking = delta.get("thinking", "") or ""
                if thinking:
                    yield PartialThinkingEvent(block_id, thinking)
                    self._saw_partial_events = True
        elif event_type == "content_block_stop":
            yield PartialFinishedEvent(block_id)
