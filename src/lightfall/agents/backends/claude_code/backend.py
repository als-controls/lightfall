"""ClaudeCodeBackend: the claude_agent_sdk turn loop behind the AgentBackend seam.

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
    SessionIdEvent,
    TaskFinishedEvent,
    TaskProgressEvent,
    TaskStartedEvent,
    TextBlockEvent,
    ThinkingBlockEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnCancelledEvent,
    TurnErrorEvent,
    TurnResultEvent,
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
        self._saw_partial_events = False
        self._current_message_id = ""

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
        return None

    async def run_turn(
        self,
        prompt: str,
        cancelled: Callable[[], bool],
        stopping: Callable[[], bool],
    ) -> AsyncIterator[AgentEvent]:
        """
        Run a Claude query and yield neutral events for its responses.

        Args:
            prompt: The query to send
            cancelled: True once the user asked to cancel this turn
            stopping: True once the worker is shutting down
        """
        # Track whether the CLI actually streamed text/thinking blocks for
        # this query. The AssistantMessage handler uses this to decide
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

        try:
            from claude_agent_sdk.types import (
                AssistantMessage,
                ResultMessage,
                StreamEvent,
                TaskNotificationMessage,
                TaskProgressMessage,
                TaskStartedMessage,
                TextBlock,
                ThinkingBlock,
                ToolResultBlock,
                ToolUseBlock,
            )

            # Send the query
            logger.info(
                "[sdk-stream] PersistentWorker query sent prompt_prefix={!r}",
                prompt[:80],
            )
            await self.client.query(prompt)

            exit_reason = "stream_exhausted"
            # Receive and process responses
            async for msg in self.client.receive_response():
                logger.info("[sdk-stream] msg {}", _summarize_sdk_msg(msg))
                # Check for stop or cancel
                if stopping() or cancelled():
                    if cancelled():
                        # Drain remaining responses so the stream is clean
                        # for the next query. Silently consume without emitting.
                        logger.info("[sdk-stream] cancel_requested — draining")
                        await self._drain_response_stream(stopping)
                        yield TurnCancelledEvent()
                        exit_reason = "cancel_requested"
                    else:
                        exit_reason = "should_stop"
                    break

                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        # Check cancel between blocks too
                        if cancelled():
                            logger.info("[sdk-stream] cancel mid-block — draining")
                            await self._drain_response_stream(stopping)
                            yield TurnCancelledEvent()
                            return

                        if isinstance(block, TextBlock):
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
                    for ev in self._dispatch_stream_event(msg):
                        yield ev

                elif isinstance(msg, TaskStartedMessage):
                    yield TaskStartedEvent(
                        msg.task_id, msg.description, msg.tool_use_id or "",
                    )
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

                elif isinstance(msg, ResultMessage):
                    logger.info(
                        "[sdk-stream] ResultMessage stop_reason={} subtype={} -> ending turn",
                        getattr(msg, "stop_reason", None),
                        getattr(msg, "subtype", None),
                    )
                    yield TurnResultEvent({
                        "total_cost_usd": msg.total_cost_usd if hasattr(msg, 'total_cost_usd') else 0,
                        "input_tokens": msg.usage.get("input_tokens", 0) if msg.usage else 0,
                        "output_tokens": msg.usage.get("output_tokens", 0) if msg.usage else 0,
                        "session_id": getattr(msg, "session_id", "") or "",
                    })
                    sid = getattr(msg, "session_id", "") or ""
                    if sid:
                        yield SessionIdEvent(sid)
                    # Refresh context-window usage for the cockpit. Best-effort:
                    # we are on the worker loop between turns, so we can await the
                    # SDK control call directly. Bounded so a wedged CLI can't
                    # stall the worker; never let it break the turn.
                    try:
                        ctx = await asyncio.wait_for(
                            self.client.get_context_usage(), timeout=2.0
                        )
                        if ctx:
                            yield ContextUsageEvent(dict(ctx))
                    except Exception as exc:  # noqa: BLE001 - best-effort telemetry
                        logger.debug("[sdk-stream] get_context_usage failed: {}", exc)
                    exit_reason = "result_message"
                    break
                else:
                    logger.info(
                        "[sdk-stream] unhandled msg type={} — loop continues",
                        type(msg).__name__,
                    )

            logger.info(
                "[sdk-stream] PersistentWorker loop exit reason={}", exit_reason
            )

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            error_msg = str(e)
            logger.exception("[sdk-stream] PersistentWorker query error")

            # Detect CLI process death (common on Windows)
            if "stream closed" in error_msg.lower() or "0xC0000005" in error_msg:
                yield TurnErrorEvent(
                    "Claude CLI process terminated unexpectedly. "
                    "This may be a transient issue — try sending another message."
                )
            else:
                yield TurnErrorEvent(f"Query error: {error_msg}\n{error_details}")

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

    async def _drain_response_stream(self, stopping: Callable[[], bool]) -> None:
        """Drain remaining responses from the CLI after cancellation.

        Consumes pending messages without emitting signals, so the stream
        is clean for the next query. The whole drain is bounded by a
        wall-clock timeout: if the CLI does not emit a terminating
        ``ResultMessage`` after we sent ``interrupt()`` (e.g. it crashed
        mid-turn), we still return control to the worker loop instead of
        hanging the cancel path indefinitely.
        """
        drained = 0
        # Tight enough that a stuck CLI can't make the UI feel frozen,
        # generous enough to let a healthy CLI flush its terminating
        # ResultMessage after an interrupt.
        drain_timeout_s = 2.0
        try:
            from claude_agent_sdk.types import ResultMessage

            async def _consume() -> None:
                nonlocal drained
                async for msg in self.client.receive_response():
                    drained += 1
                    logger.info(
                        "[sdk-stream] drain msg {}", _summarize_sdk_msg(msg)
                    )
                    if isinstance(msg, ResultMessage) or stopping():
                        return

            await asyncio.wait_for(_consume(), timeout=drain_timeout_s)
        except TimeoutError:
            logger.warning(
                "[sdk-stream] drain timed out after {}s ({} messages); "
                "giving up to keep cancel responsive",
                drain_timeout_s,
                drained,
            )
        except Exception:
            # Best-effort drain — log but don't crash on errors. Previously
            # this was a bare ``pass`` which hid CLI subprocess death and
            # JSON parse failures during cancellation.
            logger.exception(
                "[sdk-stream] drain exception after {} messages", drained
            )
        else:
            logger.info("[sdk-stream] drain complete after {} messages", drained)
