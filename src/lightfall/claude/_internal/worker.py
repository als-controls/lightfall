"""QThread worker for running async Claude Agent SDK operations."""

import asyncio
import threading
from queue import Empty, Queue
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

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


class PersistentClaudeWorker(QThread):
    """
    Persistent worker that maintains an event loop and connection to Claude.

    This worker stays alive for the lifetime of the agent, keeping the
    Claude CLI subprocess and event loop running. It accepts query requests
    via a queue and processes them sequentially.
    """

    # Signals
    message_received = Signal(str)
    thinking_received = Signal(str)
    tool_called = Signal(str, dict)
    tool_result = Signal(str, dict)
    error_occurred = Signal(str)
    query_completed = Signal()
    query_cancelled = Signal()  # Emitted when a query is cancelled
    result_received = Signal(dict)
    connected = Signal()
    # Partial streaming (content_block_* events from StreamEvent)
    partial_block_started = Signal(str, str)  # block_id, kind
    partial_text = Signal(str, str)           # block_id, delta
    partial_thinking = Signal(str, str)       # block_id, delta
    partial_block_finished = Signal(str)      # block_id
    # Task tool subagent lifecycle (Task*Message)
    task_started = Signal(str, str, str)            # task_id, description, tool_use_id
    task_progress = Signal(str, str, dict, str)     # task_id, description, usage, last_tool
    task_finished = Signal(str, str, str, str, dict)  # task_id, status, summary, output_file, usage

    def __init__(self, client: Any, initial_prompt: str | None = None, permission_manager: Any | None = None, parent: QObject | None = None):
        """
        Initialize the persistent worker.

        Args:
            client: ClaudeSDKClient instance (not yet connected)
            initial_prompt: Optional initial prompt to send on connection
            permission_manager: Optional PermissionManager for cancelling pending approvals
            parent: Parent QObject
        """
        super().__init__(parent)
        self.client = client
        self.initial_prompt = initial_prompt
        self._permission_manager = permission_manager
        self._query_queue: Queue = Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._should_stop = False
        self._is_connected = False
        self._is_processing = False  # True when actively processing a query
        self._cancel_requested = False  # True when cancel is requested
        self._shutdown_event: asyncio.Event | None = None  # Created in run()
        self._processing_stopped = threading.Event()  # Signals when loop exits

    def run(self) -> None:
        """
        Thread entry point. Creates event loop, connects, and processes queries.
        """
        try:
            # Create persistent event loop
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._shutdown_event = asyncio.Event()

            # Connect to Claude
            logger.debug("PersistentClaudeWorker: Attempting to connect...")
            self._loop.run_until_complete(self._connect())
            self._is_connected = True
            logger.debug("PersistentClaudeWorker: Connected successfully")
            self.connected.emit()

            # Process queries until stopped
            logger.debug("PersistentClaudeWorker: Starting query processing loop...")
            self._loop.run_until_complete(self._process_queries())

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error("PersistentClaudeWorker error: {}", error_details)
            self.error_occurred.emit(f"Worker error: {str(e)}\n{error_details}")

        finally:
            self._should_stop = True
            self._processing_stopped.set()
            if self._loop:
                # Cancel pending tasks gracefully
                try:
                    pending = asyncio.all_tasks(self._loop)
                    for task in pending:
                        task.cancel()
                    # Let cancellations propagate
                    if pending:
                        self._loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                except Exception:
                    pass  # Loop may already be stopping
                finally:
                    try:
                        self._loop.close()
                    except Exception:
                        pass
            logger.debug("PersistentClaudeWorker: Stopped")

    async def _connect(self) -> None:
        """Connect to Claude Agent SDK."""
        await self.client.connect(prompt=self.initial_prompt)

    async def _process_queries(self) -> None:
        """Process queries from the queue.

        Uses a simple poll loop with asyncio.sleep instead of run_in_executor,
        which avoids "cannot schedule new futures after shutdown" errors during
        application exit.
        """
        try:
            while not self._should_stop:
                try:
                    prompt = self._query_queue.get_nowait()
                except Empty:
                    await asyncio.sleep(0.1)
                    continue

                logger.debug("Processing query: {}...", prompt[:50])
                await self._run_query(prompt)

        except asyncio.CancelledError:
            pass  # Clean shutdown
        finally:
            self._processing_stopped.set()

    async def _run_query(self, prompt: str) -> None:
        """
        Run a Claude query and process responses.

        Args:
            prompt: The query to send
        """
        self._is_processing = True
        self._cancel_requested = False
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
                if self._should_stop or self._cancel_requested:
                    if self._cancel_requested:
                        # Drain remaining responses so the stream is clean
                        # for the next query. Silently consume without emitting.
                        logger.info("[sdk-stream] cancel_requested — draining")
                        await self._drain_response_stream()
                        self.query_cancelled.emit()
                        exit_reason = "cancel_requested"
                    else:
                        exit_reason = "should_stop"
                    break

                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        # Check cancel between blocks too
                        if self._cancel_requested:
                            logger.info("[sdk-stream] cancel mid-block — draining")
                            await self._drain_response_stream()
                            self.query_cancelled.emit()
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
                                self.message_received.emit(block.text)
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
                                self.thinking_received.emit(thinking_text)
                        elif isinstance(block, ToolUseBlock):
                            logger.info(
                                "[sdk-stream] ToolUseBlock name={} id={}",
                                block.name,
                                getattr(block, "id", None),
                            )
                            self.tool_called.emit(block.name, block.input)
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
                            self.tool_result.emit(
                                block.tool_use_id,
                                {"content": block.content, "is_error": block.is_error}
                            )
                        else:
                            logger.info(
                                "[sdk-stream] unknown block type={}",
                                type(block).__name__,
                            )

                elif isinstance(msg, StreamEvent):
                    self._dispatch_stream_event(msg)

                elif isinstance(msg, TaskStartedMessage):
                    self.task_started.emit(
                        msg.task_id, msg.description, msg.tool_use_id or "",
                    )
                elif isinstance(msg, TaskProgressMessage):
                    self.task_progress.emit(
                        msg.task_id, msg.description,
                        dict(msg.usage) if msg.usage else {},
                        msg.last_tool_name or "",
                    )
                elif isinstance(msg, TaskNotificationMessage):
                    self.task_finished.emit(
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
                    self.result_received.emit({
                        "total_cost_usd": msg.total_cost_usd if hasattr(msg, 'total_cost_usd') else 0,
                        "input_tokens": msg.usage.get("input_tokens", 0) if msg.usage else 0,
                        "output_tokens": msg.usage.get("output_tokens", 0) if msg.usage else 0,
                    })
                    self.query_completed.emit()
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
                self.error_occurred.emit(
                    "Claude CLI process terminated unexpectedly. "
                    "This may be a transient issue — try sending another message."
                )
            else:
                self.error_occurred.emit(f"Query error: {error_msg}\n{error_details}")

        finally:
            self._is_processing = False
            self._cancel_requested = False

    def _dispatch_stream_event(self, msg: Any) -> None:
        """Parse a StreamEvent and emit per-block partial_* signals.

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
            self.partial_block_started.emit(block_id, kind)
            # Some CLI modes (e.g. thinking={display:summarized}) ship the
            # whole block content in the start event with no following
            # deltas. Treat that as a single big delta.
            initial = (
                block.get("text", "") if kind == "text"
                else block.get("thinking", "")
            ) or ""
            if initial:
                if kind == "text":
                    self.partial_text.emit(block_id, initial)
                else:
                    self.partial_thinking.emit(block_id, initial)
                self._saw_partial_events = True
        elif event_type == "content_block_delta":
            delta = event.get("delta", {}) or {}
            dtype = delta.get("type", "")
            if dtype == "text_delta":
                text = delta.get("text", "") or ""
                if text:
                    self.partial_text.emit(block_id, text)
                    self._saw_partial_events = True
            elif dtype == "thinking_delta":
                thinking = delta.get("thinking", "") or ""
                if thinking:
                    self.partial_thinking.emit(block_id, thinking)
                    self._saw_partial_events = True
        elif event_type == "content_block_stop":
            self.partial_block_finished.emit(block_id)

    async def _drain_response_stream(self) -> None:
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
                    if isinstance(msg, ResultMessage) or self._should_stop:
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

    def send_query(self, prompt: str) -> None:
        """
        Queue a query to be processed.

        Args:
            prompt: The query to send
        """
        if self._is_connected:
            logger.debug("Queuing query: {}...", prompt[:50])
            self._query_queue.put(prompt)
        else:
            logger.warning("Cannot send query — not connected!")

    def cancel_current_query(self) -> bool:
        """
        Request cancellation of the current query.

        Sets the cancel flag, releases any pending permission prompt, and —
        critically — dispatches the SDK's ``interrupt()`` control message on
        the worker's event loop. Without that interrupt, the worker stays
        blocked in ``async for msg in client.receive_response()`` until the
        CLI happens to emit the next message, which can be many seconds
        while the model is generating or running a tool. ``interrupt()``
        tells the CLI to end the turn now, so the read unblocks promptly
        and the flag check at the top of the loop fires.

        Returns:
            True if a query was being processed and cancel was requested.
        """
        if self._is_processing:
            self._cancel_requested = True
            # Cancel any pending permission requests so we're not stuck
            # waiting for user approval on a query they want to cancel
            if self._permission_manager is not None:
                self._permission_manager.cancel_all_pending()
            self._request_sdk_interrupt()
            return True
        return False

    def _request_sdk_interrupt(self) -> None:
        """Schedule ``client.interrupt()`` on the worker's event loop.

        Safe to call from any thread. Best-effort: logs and swallows any
        failure (loop closed, transport gone, control request timeout)
        because the cancel flag and permission-cancel paths still ensure
        we terminate eventually; the interrupt just makes it fast.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.client.interrupt(), loop
            )
        except RuntimeError:
            # Loop stopped between the check above and the schedule call.
            return

        def _log_if_failed(fut: Any) -> None:
            exc = fut.exception()
            if exc is not None:
                logger.warning(
                    "Claude SDK interrupt() raised: {}", exc
                )

        future.add_done_callback(_log_if_failed)

    @property
    def is_processing(self) -> bool:
        """Check if a query is currently being processed."""
        return self._is_processing

    def stop(self) -> None:
        """Stop the worker and close the connection."""
        self._should_stop = True
        self._cancel_requested = True  # Also cancel any current query
        # If a query is in flight, send interrupt so receive_response()
        # unblocks promptly instead of waiting on the next CLI message.
        if self._is_processing:
            self._request_sdk_interrupt()
        # Signal the shutdown event if we have a loop
        if self._loop and self._shutdown_event:
            try:
                self._loop.call_soon_threadsafe(self._shutdown_event.set)
            except RuntimeError:
                pass  # Loop already closed
