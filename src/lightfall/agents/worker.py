"""Generic QThread worker that runs an AgentBackend's async turn loop.

Owns the thread, event loop, query queue and cancel/stop flags, and maps the
backend's neutral :mod:`lightfall.agents.backend` events onto Qt signals.

Two drive modes, picked from the backend's shape:

- turn-based (``run_turn``): one prompt at a time, each turn read to its end.
- session-stream (``session_stream = True``): one reader consumes the
  backend's event stream for the session's whole life and prompts are sent
  as they arrive. The stream also carries turns the session starts itself
  (background-task notifications), and a prompt sent mid-turn joins the
  running turn, so prompts and results don't pair up. Busy/idle is derived
  instead: busy while a turn runs or any sent prompt has not yet been taken
  in (PromptAcceptedEvent); the "query finished" signals fire when a turn
  ends with nothing of ours still waiting.
"""
from __future__ import annotations

import asyncio
import threading
import time
from queue import Empty, Queue
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from lightfall.agents.backend import (
    AgentBackend,
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
    TurnCancelledEvent,
    TurnErrorEvent,
    TurnResultEvent,
    TurnStartedEvent,
)
from lightfall.utils.logging import logger

# Session-stream mode: how long after interrupt() the cancelled turn's result
# may take before the worker declares the turn cancelled anyway.
CANCEL_SETTLE_S = 3.0
# Session-stream mode: a sent prompt the session neither took in nor started a
# turn for within this long (stream silent throughout) is given up on, so the
# UI can't stay busy forever. The CLI normally starts its turn within ~1 s.
PROMPT_STALL_S = 60.0


class AgentWorker(QThread):
    """
    Persistent worker that maintains an event loop and an agent backend session.

    This worker stays alive for the lifetime of the agent, keeping the
    backend (e.g. the Claude CLI subprocess) and event loop running. It
    accepts query requests via a queue and processes them sequentially.
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
    context_usage = Signal(dict)  # ContextUsageResponse after each turn
    session_id_changed = Signal(str)  # current session id from ResultMessage
    # Session-stream mode: a turn began. True when no prompt of ours is
    # waiting, i.e. the session started this turn itself (e.g. a background
    # task finished while idle).
    turn_started = Signal(bool)
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

    def __init__(
        self,
        backend: AgentBackend,
        permission_manager: Any | None = None,
        parent: QObject | None = None,
    ):
        """
        Initialize the persistent worker.

        Args:
            backend: AgentBackend instance (not yet connected)
            permission_manager: Optional PermissionManager for cancelling pending approvals
            parent: Parent QObject
        """
        super().__init__(parent)
        self.backend = backend
        self._permission_manager = permission_manager
        self._query_queue: Queue = Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._should_stop = False
        self._is_connected = False
        self._is_processing = False  # True when actively processing a query
        self._cancel_requested = False  # True when cancel is requested
        # set_model requested mid-turn or before connect() finished
        self._pending_model: tuple[str | None] | None = None
        # Orders _is_connected against the request_set_* deferral checks.
        self._connect_lock = threading.Lock()
        # set_effort requested mid-turn or before connect() finished
        self._pending_effort: tuple[str | None] | None = None
        # Session-stream mode state (see module docstring). The pending count
        # is bumped on the caller's thread in send_query, so is_processing
        # never reads idle between "queued" and "sent".
        self._session_mode = bool(getattr(backend, "session_stream", False))
        self._pending_lock = threading.Lock()
        self._pending_prompts = 0
        self._in_turn = False
        self._cancel_at: float | None = None  # monotonic time of cancel request
        self._cancel_forced = False  # gave up waiting for the cancelled turn's result
        self._session_ended = False
        self._last_activity = time.monotonic()
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

            # Connect the backend
            logger.debug("AgentWorker: Attempting to connect...")
            self._loop.run_until_complete(self._connect())
            with self._connect_lock:
                self._is_connected = True
            self._loop.run_until_complete(self._apply_deferred())
            logger.debug("AgentWorker: Connected successfully")
            self.connected.emit()

            # Process queries until stopped
            logger.debug("AgentWorker: Starting query processing loop...")
            if self._session_mode:
                self._loop.run_until_complete(self._run_session())
            else:
                self._loop.run_until_complete(self._process_queries())

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error("AgentWorker error: {}", error_details)
            self.error_occurred.emit(f"Worker error: {str(e)}\n{error_details}")

        finally:
            self._should_stop = True
            self._processing_stopped.set()
            if self._loop and not self._loop.is_closed():
                try:
                    self._loop.run_until_complete(self.backend.close())
                except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                    logger.warning("AgentWorker: backend close() raised: {}", exc)
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
            logger.debug("AgentWorker: Stopped")

    async def _connect(self) -> None:
        """Connect the agent backend."""
        await self.backend.connect()

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
        self._is_processing = True
        self._cancel_requested = False
        self._turn_terminated = False
        try:
            async for ev in self.backend.run_turn(
                prompt,
                cancelled=lambda: self._cancel_requested,
                stopping=lambda: self._should_stop,
            ):
                self._emit(ev)
            # The backend contract ends every turn with a result, cancelled or
            # error event unless the worker is stopping. A backend whose stream
            # simply closes (CLI exited mid-turn) would otherwise leave the UI
            # in its "processing" state forever.
            if not self._turn_terminated and not self._should_stop:
                if self._cancel_requested:
                    self.query_cancelled.emit()
                else:
                    logger.warning("AgentWorker: turn ended without a terminal event")
                    self.error_occurred.emit(
                        "The agent's response stream ended without a result "
                        "(backend closed mid-turn)."
                    )
        except Exception as e:  # backend bug: never kill the worker loop
            import traceback
            logger.exception("AgentWorker: backend run_turn raised")
            self.error_occurred.emit(f"Query error: {e}\n{traceback.format_exc()}")
        finally:
            self._is_processing = False
            self._cancel_requested = False
            await self._apply_deferred()

    # --- session-stream mode -------------------------------------------------

    def _pending(self) -> int:
        with self._pending_lock:
            return self._pending_prompts

    def _set_pending(self, n: int) -> None:
        with self._pending_lock:
            self._pending_prompts = max(0, n)

    def _dec_pending(self) -> None:
        with self._pending_lock:
            self._pending_prompts = max(0, self._pending_prompts - 1)

    def _clear_cancel(self) -> None:
        self._cancel_at = None
        self._cancel_forced = False
        self._cancel_requested = False

    async def _run_session(self) -> None:
        """Send queued prompts as they come while one task reads the stream."""
        reader = asyncio.ensure_future(self._read_session())
        try:
            while not self._should_stop:
                try:
                    prompt = self._query_queue.get_nowait()
                except Empty:
                    await self._session_watchdog()
                    await asyncio.sleep(0.1)
                    continue
                self._last_activity = time.monotonic()
                if self._session_ended:
                    self._dec_pending()
                    self.error_occurred.emit(
                        "The agent session has ended; reset the conversation "
                        "to start a new one."
                    )
                    continue
                try:
                    await self.backend.send(prompt)
                except Exception as e:  # noqa: BLE001 - never kill the worker loop
                    logger.exception("AgentWorker: backend send failed")
                    self._dec_pending()
                    self.error_occurred.emit(f"Could not send the message to the agent: {e}")
        except asyncio.CancelledError:
            pass  # Clean shutdown
        finally:
            reader.cancel()
            try:
                await reader
            except BaseException:  # noqa: BLE001 - cancelled/failed reader at shutdown
                pass
            self._processing_stopped.set()

    async def _read_session(self) -> None:
        errored = False
        try:
            async for ev in self.backend.events(lambda: self._should_stop):
                self._last_activity = time.monotonic()
                if isinstance(ev, TurnErrorEvent):
                    errored = True
                await self._on_session_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # backend bug: report, don't kill the worker
            import traceback
            logger.exception("AgentWorker: backend events() raised")
            errored = True
            self.error_occurred.emit(f"Agent stream error: {e}\n{traceback.format_exc()}")
        if self._should_stop:
            return
        self._session_ended = True
        self._reset_session_state()
        if not errored:
            logger.warning("AgentWorker: session stream ended")
            self.error_occurred.emit(
                "The agent's response stream ended (backend process exited)."
            )

    def _reset_session_state(self) -> None:
        self._set_pending(0)
        self._in_turn = False
        self._clear_cancel()

    async def _on_session_event(self, ev: AgentEvent) -> None:
        if isinstance(ev, TurnStartedEvent):
            self._in_turn = True
            if self._cancel_forced:
                # The abandoned turn's leftovers are over; this is a new one.
                self._clear_cancel()
            self.turn_started.emit(self._pending() == 0)
        elif isinstance(ev, PromptAcceptedEvent):
            self._dec_pending()
            if self._cancel_forced:
                # A new prompt joined the abandoned turn: show its output.
                self._clear_cancel()
        elif isinstance(ev, TurnResultEvent):
            self._in_turn = False
            if self._cancel_at is not None or ev.info.get("cancelled"):
                forced = self._cancel_forced
                self._clear_cancel()
                if not forced:
                    self.query_cancelled.emit()
            else:
                self.result_received.emit(ev.info)
                if self._pending() == 0:
                    self.query_completed.emit()
            if not self.is_processing:
                await self._apply_deferred()
        elif isinstance(ev, TurnErrorEvent):
            self._reset_session_state()
            self.error_occurred.emit(ev.message)
        elif isinstance(
            ev, (SessionIdEvent, ContextUsageEvent,
                 TaskStartedEvent, TaskProgressEvent, TaskFinishedEvent),
        ):
            self._emit(ev)
        elif isinstance(ev, TurnCancelledEvent):
            logger.warning("AgentWorker: TurnCancelledEvent in session mode ignored")
        elif self._cancel_at is not None:
            pass  # the cancelled turn's remaining output: drop it
        else:
            self._emit(ev)

    async def _session_watchdog(self) -> None:
        """Unstick the UI if the session never answers a cancel or a prompt."""
        now = time.monotonic()
        if self._cancel_at is not None:
            if not self._cancel_forced and now - self._cancel_at > CANCEL_SETTLE_S:
                logger.warning(
                    "AgentWorker: no result {}s after interrupt; treating the turn "
                    "as cancelled", CANCEL_SETTLE_S,
                )
                self._cancel_forced = True  # keep dropping its late output
                self._in_turn = False
                self._set_pending(0)
                self.query_cancelled.emit()
                await self._apply_deferred()
        elif (
            not self._in_turn
            and self._pending() > 0
            and now - self._last_activity > PROMPT_STALL_S
        ):
            logger.warning(
                "AgentWorker: {} prompt(s) not taken in after {}s of silence; giving up",
                self._pending(), PROMPT_STALL_S,
            )
            self._set_pending(0)
            self.error_occurred.emit(
                f"The agent did not start on the message within {PROMPT_STALL_S:.0f} s."
            )

    async def _apply_deferred(self) -> None:
        """Apply set_model / set_effort requests held during a turn or the connect."""
        with self._connect_lock:
            model, self._pending_model = self._pending_model, None
            effort, self._pending_effort = self._pending_effort, None
        if model is not None:
            try:
                await self.backend.set_model(model[0])
            except Exception:  # noqa: BLE001 - never kill the worker loop
                logger.exception("AgentWorker: deferred set_model failed")
        if effort is not None:
            try:
                await self.backend.set_effort(effort[0])
            except Exception:  # noqa: BLE001 - never kill the worker loop
                logger.exception("AgentWorker: deferred set_effort failed")

    def _emit(self, ev: AgentEvent) -> None:
        """Map one neutral event onto the legacy signal set."""
        if isinstance(ev, TextBlockEvent):
            self.message_received.emit(ev.text)
        elif isinstance(ev, ThinkingBlockEvent):
            self.thinking_received.emit(ev.text)
        elif isinstance(ev, ToolCallEvent):
            self.tool_called.emit(ev.name, ev.input)
        elif isinstance(ev, ToolResultEvent):
            self.tool_result.emit(ev.tool_use_id, ev.payload)
        elif isinstance(ev, PartialStartedEvent):
            self.partial_block_started.emit(ev.block_id, ev.kind)
        elif isinstance(ev, PartialTextEvent):
            self.partial_text.emit(ev.block_id, ev.delta)
        elif isinstance(ev, PartialThinkingEvent):
            self.partial_thinking.emit(ev.block_id, ev.delta)
        elif isinstance(ev, PartialFinishedEvent):
            self.partial_block_finished.emit(ev.block_id)
        elif isinstance(ev, TaskStartedEvent):
            self.task_started.emit(ev.task_id, ev.description, ev.tool_use_id)
        elif isinstance(ev, TaskProgressEvent):
            self.task_progress.emit(ev.task_id, ev.description, ev.usage, ev.last_tool)
        elif isinstance(ev, TaskFinishedEvent):
            self.task_finished.emit(
                ev.task_id, ev.status, ev.summary, ev.output_file, ev.usage,
            )
        elif isinstance(ev, TurnResultEvent):
            self._turn_terminated = True
            self.result_received.emit(ev.info)
            self.query_completed.emit()
        elif isinstance(ev, SessionIdEvent):
            self.session_id_changed.emit(ev.session_id)
        elif isinstance(ev, ContextUsageEvent):
            self.context_usage.emit(ev.usage)
        elif isinstance(ev, TurnCancelledEvent):
            self._turn_terminated = True
            self.query_cancelled.emit()
        elif isinstance(ev, TurnErrorEvent):
            self._turn_terminated = True
            self.error_occurred.emit(ev.message)
        else:
            logger.warning("AgentWorker: unknown event {!r}", ev)

    def send_query(self, prompt: str) -> None:
        """
        Queue a query to be processed.

        Args:
            prompt: The query to send
        """
        if self._is_connected:
            logger.debug("Queuing query: {}...", prompt[:50])
            if self._session_mode:
                with self._pending_lock:
                    self._pending_prompts += 1
            self._query_queue.put(prompt)
        else:
            logger.warning("Cannot send query — not connected!")

    def cancel_current_query(self) -> bool:
        """
        Request cancellation of the current query.

        Sets the cancel flag, releases any pending permission prompt, and —
        critically — dispatches ``backend.interrupt()`` on the worker's event loop so the
        backend's turn stream unblocks promptly rather than waiting for its next
        event, which can be many seconds while the model is generating or running
        a tool. The flag check at the top of the loop then fires.

        Returns:
            True if a query was being processed and cancel was requested.
        """
        if self.is_processing:
            self._cancel_requested = True
            if self._session_mode and self._cancel_at is None:
                self._cancel_at = time.monotonic()
            # Cancel any pending permission requests so we're not stuck
            # waiting for user approval on a query they want to cancel
            if self._permission_manager is not None:
                self._permission_manager.cancel_all_pending()
            self._request_sdk_interrupt()
            return True
        return False

    def _run_on_loop(self, coro_factory, label: str) -> None:
        """Schedule a backend coroutine on the worker's event loop.

        Safe to call from any thread. Best-effort: logs and swallows failures
        (loop closed, transport gone, control-request timeout). ``coro_factory``
        is a zero-arg callable returning the coroutine, so we only create the
        coroutine once we know the loop is live (avoids 'coroutine was never
        awaited' warnings when the loop is gone).
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        except RuntimeError:
            # Loop stopped between the check above and the schedule call.
            return

        def _log_if_failed(fut: Any) -> None:
            exc = fut.exception()
            if exc is not None:
                logger.warning("Agent backend {} raised: {}", label, exc)

        future.add_done_callback(_log_if_failed)

    def _request_sdk_interrupt(self) -> None:
        """Schedule ``backend.interrupt()`` on the worker's event loop."""
        self._run_on_loop(lambda: self.backend.interrupt(), "interrupt()")

    def request_set_model(self, model: str | None) -> None:
        """Schedule ``backend.set_model(model)`` on the worker's event loop.

        Live model switch — no reconnect. Takes effect on the next turn. A
        request during a turn, or before ``connect()`` has finished, is held and
        applied when the turn ends / the connection is up.
        """
        with self._connect_lock:
            if self.is_processing or not self._is_connected:
                logger.debug("set_model({}) deferred until the worker is idle", model)
                self._pending_model = (model,)
                return
        self._run_on_loop(lambda: self.backend.set_model(model), f"set_model({model})")

    def request_set_effort(self, effort: str | None) -> None:
        """Schedule ``backend.set_effort(effort)`` on the worker's event loop.

        Only for backends whose caps have ``live_set_effort`` (agy). Takes
        effect on the next turn; a request during a turn, or before ``connect()``
        has finished, is held until the turn ends / the connection is up.
        """
        with self._connect_lock:
            if self.is_processing or not self._is_connected:
                logger.debug("set_effort({}) deferred until the worker is idle", effort)
                self._pending_effort = (effort,)
                return
        self._run_on_loop(lambda: self.backend.set_effort(effort), f"set_effort({effort})")

    @property
    def is_processing(self) -> bool:
        """True while a turn runs or a sent prompt awaits an answer."""
        if self._session_mode:
            return (
                self._pending() > 0
                or self._in_turn
                or (self._cancel_at is not None and not self._cancel_forced)
            )
        return self._is_processing

    def stop(self) -> None:
        """Stop the worker and close the connection."""
        self._should_stop = True
        self._cancel_requested = True  # Also cancel any current query
        # If a query is in flight, send interrupt so receive_response()
        # unblocks promptly instead of waiting on the next CLI message.
        if self.is_processing:
            self._request_sdk_interrupt()
        # Signal the shutdown event if we have a loop
        if self._loop and self._shutdown_event:
            try:
                self._loop.call_soon_threadsafe(self._shutdown_event.set)
            except RuntimeError:
                pass  # Loop already closed
