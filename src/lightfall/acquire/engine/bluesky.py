"""Bluesky RunEngine implementation.

Provides a Qt-integrated wrapper around Bluesky's RunEngine.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bluesky import RunEngine
from bluesky.utils import DuringTask, RunEngineInterrupted
from PySide6.QtCore import QThread, Signal

from lightfall.acquire.engine.base import BaseEngine, PrioritizedProcedure
from lightfall.acquire.engine.state import EngineState
from lightfall.acquire.engine.waiting_hook import WaitingHookBridge
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture, invoke_in_main_thread

# Map Bluesky RunEngine state strings to EngineState
_RE_STATE_MAP: dict[str, EngineState] = {
    "idle": EngineState.IDLE,
    "running": EngineState.RUNNING,
    "paused": EngineState.PAUSED,
    "stopping": EngineState.STOPPING,
    "aborting": EngineState.ABORTING,
}

if TYPE_CHECKING:
    pass

__all__ = ["BlueskyEngine"]


class BlueskyEngine(BaseEngine):
    """Bluesky RunEngine wrapped as an NCS Engine.

    Procedures are Bluesky plan generators (functions yielding Msg objects).
    Output documents follow the Bluesky event model with (name, doc) pairs.

    This class provides:
    - Background thread execution with dedicated asyncio event loop
    - Priority-based plan queuing
    - Qt signals for all state changes
    - Document streaming via signals and callbacks

    Signals (inherited from BaseEngine):
        sigOutput(str, dict): Emitted for each document (name, doc).
        sigStart(): Emitted when a plan starts executing.
        sigFinish(): Emitted when a plan finishes successfully.
        sigPause(): Emitted when execution is paused.
        sigResume(): Emitted when execution resumes.
        sigAbort(): Emitted when execution is aborted.
        sigException(Exception): Emitted when an error occurs.
        sigReady(): Emitted when queue is empty and RE is idle.
        sigStateChanged(str): Emitted on any state change.

    Backward Compatibility:
        sigDocumentYield: Alias for sigOutput.

    Example:
        engine = BlueskyEngine()
        engine.sigOutput.connect(handle_document)
        engine.sigFinish.connect(on_scan_complete)

        # Queue a scan
        from bluesky.plans import scan
        engine(scan([detector], motor, 0, 10, 11))

        # Or with priority (lower = higher priority)
        engine.submit(scan([detector], motor, 0, 10, 11), priority=0)
    """

    # Backward compatibility alias
    sigDocumentYield = Signal(str, dict)

    def __init__(self, *, toast_notifications: bool = True, **kwargs: Any) -> None:
        """Initialize the BlueskyEngine.

        Args:
            toast_notifications: Whether to show toast notifications on run
                completion, abort, and errors. Defaults to True.
            **kwargs: Additional arguments passed to Bluesky's RunEngine.
        """
        super().__init__(name="bluesky", toast_notifications=toast_notifications)

        self._RE: RunEngine | None = None
        self._adopted: bool = False
        self._re_kwargs = kwargs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._kwargs_callables: set[Callable[[], dict[str, Any]]] = set()
        self._resume_future: QThreadFuture | None = None
        self._interrupt_future: QThreadFuture | None = None
        self._queue_future: QThreadFuture | None = None
        self._waiting_bridge = WaitingHookBridge()

        # Connect sigOutput to sigDocumentYield for backward compatibility
        self.sigOutput.connect(self.sigDocumentYield.emit)

        # Start the queue processing thread
        self._start_queue_processor()

    @property
    def RE(self) -> RunEngine | None:
        """Access the underlying Bluesky RunEngine."""
        return self._RE

    def adopt(self, run_engine: RunEngine) -> None:
        """Adopt an externally-created RunEngine instead of building one.

        Used when another environment (e.g. an NSLS-II profile-collection run
        in the embedded console) has already created a RunEngine that carries
        its own document subscriptions (Kafka, TiledWriter), preprocessors
        (SupplementalData), and metadata store (Redis-backed ``RE.md``). Those
        must be preserved, so this seeds the engine with that exact object
        rather than constructing a fresh one.

        Wires the engine's waiting-hook bridge and document stream onto the
        adopted RE and marks the engine adopted so the background queue
        processor will not create or overwrite it.

        Args:
            run_engine: The already-configured Bluesky RunEngine to drive.
        """
        self._RE = run_engine
        run_engine.waiting_hook = self._waiting_bridge
        run_engine.subscribe(lambda name, doc: self._emit_output(name, doc))
        logger.info("[bluesky] Adopted external RunEngine")
        self._adopted = True
        self._set_state(EngineState.IDLE)

    @property
    def waiting_bridge(self) -> WaitingHookBridge:
        """Access the waiting hook bridge for connecting progress UI."""
        return self._waiting_bridge

    @property
    def state(self) -> EngineState:
        """Current engine state mapped from Bluesky state."""
        if self._RE is None:
            return EngineState.IDLE

        bs_state = self._RE.state
        mapping = {
            "idle": EngineState.IDLE,
            "running": EngineState.RUNNING,
            "paused": EngineState.PAUSED,
            "stopping": EngineState.STOPPING,
            "aborting": EngineState.ABORTING,
            "panicked": EngineState.ERROR,
        }
        return mapping.get(bs_state, EngineState.IDLE)

    @property
    def state_name(self) -> str:
        """Return Bluesky state string directly for compatibility."""
        if self._RE is None:
            return "unknown"
        return str(self._RE.state)

    @property
    def is_idle(self) -> bool:
        """Check if the RunEngine is idle."""
        return self._RE is not None and self._RE.state == "idle"

    def _start_queue_processor(self) -> None:
        """Start the background thread that processes the plan queue."""
        self._queue_future = QThreadFuture(
            self._process_queue,
            key="bluesky_engine",
            name="Bluesky Engine Processor",
        )
        self._queue_future.start()

    def _process_queue(self) -> None:
        """Background thread loop that processes queued plans.

        On the non-adopted path (when :py:meth:`adopt` was not called) this
        method creates its own asyncio event loop and a fresh RunEngine
        instance. On the adopted path it reuses the externally-provided
        RunEngine that was wired in by :py:meth:`adopt`. In both cases the
        loop then runs indefinitely, processing plans from the priority queue.
        """
        from queue import Empty

        if not self._adopted:
            # Create dedicated event loop for this thread
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            # Create the RunEngine
            self._RE = RunEngine(
                context_managers=[],
                during_task=DuringTask(),
                loop=self._loop,
                **self._re_kwargs,
            )

            # Wire up waiting hook for device progress tracking
            self._RE.waiting_hook = self._waiting_bridge

            # Subscribe to document stream
            self._RE.subscribe(lambda name, doc: self._emit_output(name, doc))

            logger.info("[bluesky] RunEngine initialized and ready")
            self._set_state(EngineState.IDLE)
        else:
            logger.info("[bluesky] Queue processor using adopted RunEngine")

        # Main processing loop - check for interruption request to allow clean shutdown
        while not QThread.currentThread().isInterruptionRequested():
            try:
                # Block on the priority queue with timeout. Deliberately NOT
                # under the queue lock — remove/priority operations must be
                # able to proceed while this blocks.
                item = self._queue.get(block=True, timeout=0.1)
            except Empty:
                continue

            # The item may have been removed from the queue (or be a stale
            # duplicate left by a priority rebuild) between the blocking
            # get() and now — claim it under the lock or skip it.
            if not self._claim_queued_item(item):
                self._queue.task_done()
                continue

            try:
                self._execute_plan(item)
            except Exception as ex:
                logger.error(
                    "[bluesky] Unhandled exception in queue processor "
                    "for plan '{}': {}",
                    item.name, ex,
                )
                logger.exception(ex)
                self._clear_current_procedure()
                self.sigException.emit(ex)
            finally:
                self._queue.task_done()

        logger.info("[bluesky] RunEngine processor shutting down")

    def _execute_plan(self, item: PrioritizedProcedure) -> None:
        """Execute a single plan from the queue.

        Args:
            item: The prioritized procedure containing the plan and kwargs.
        """
        plan, kwargs = item.procedure, item.kwargs.copy()

        # Inject metadata from registered callables
        for kwargs_callable in self._kwargs_callables:
            try:
                kwargs.update(kwargs_callable())
            except Exception as ex:
                logger.warning(f"[bluesky] Error in kwargs callable: {ex}")

        # Extract control kwargs that must NOT be forwarded to the RunEngine.
        # `on_complete` is a completion callback (invoked with a success flag
        # once the plan settles), not run metadata. Anything passed through to
        # RunEngine.__call__(**metadata_kw) is folded into the start document,
        # and a function object there cannot be JSON-serialized by Tiled — the
        # run would silently fail to persist.
        on_complete = kwargs.pop("on_complete", None)

        # Defense-in-depth: any remaining callable kwarg would likewise poison
        # the start document and drop the run from Tiled. Strip and warn rather
        # than losing the acquisition.
        for key in [k for k, v in kwargs.items() if callable(v)]:
            logger.warning(
                "[bluesky] Dropping non-serializable kwarg '{}'={!r}; callables "
                "cannot be stored in the run start document",
                key, kwargs[key],
            )
            kwargs.pop(key)

        self.sigStart.emit()
        self._set_state(EngineState.RUNNING)
        logger.debug(f"[bluesky] Starting plan '{item.name}' with kwargs={kwargs}")

        # RE is guaranteed to exist here - created in _process_queue before this method is called
        assert self._RE is not None

        success = False
        try:
            self._RE(plan, **kwargs)
        except RunEngineInterrupted:
            logger.info("[bluesky] Plan was interrupted by user")
            self.sigAbort.emit()
        except Exception as ex:
            logger.error(f"[bluesky] Error during plan execution: {ex}")
            logger.exception(ex)
            self.sigException.emit(ex)
        else:
            success = True
            self.sigFinish.emit()
        finally:
            self._clear_current_procedure()
            re_state = self._RE.state if self._RE else "idle"
            mapped = _RE_STATE_MAP.get(re_state, EngineState.IDLE)
            self._set_state(mapped)
            if on_complete is not None:
                self._invoke_on_complete(on_complete, success)

    def _invoke_on_complete(
        self, callback: Callable[[bool], None], success: bool
    ) -> None:
        """Invoke a ``submit(on_complete=...)`` callback on the GUI thread.

        Plans are executed on the engine worker thread, but completion
        callbacks frequently touch Qt widgets (e.g. resuming camera TV mode),
        so the call is marshalled to the main thread. Exceptions in the
        callback are logged and swallowed — a misbehaving callback must not
        take down the engine processor.
        """
        def _run() -> None:
            try:
                callback(success)
            except Exception as ex:
                logger.warning(f"[bluesky] on_complete callback raised: {ex}")

        try:
            invoke_in_main_thread(_run)
        except Exception as ex:
            logger.warning(f"[bluesky] Failed to dispatch on_complete callback: {ex}")

    # === Control Operations ===

    def pause(self, defer: bool = False) -> None:
        """Pause the currently running plan.

        Args:
            defer: If True, pause at next checkpoint. If False, pause immediately.
        """
        if self._RE is None:
            return

        if self._RE.state != "paused":
            logger.info(f"[bluesky] Pausing plan (defer={defer})")
            self._RE.request_pause(defer)
            self.sigPause.emit()
            self._set_state(EngineState.PAUSED)

    def resume(self) -> None:
        """Resume a paused plan."""
        if self._RE is None or self._RE.state != "paused":
            return

        logger.info("[bluesky] Resuming plan")

        # Run resume in a separate thread to not block
        self._resume_future = QThreadFuture(
            self._RE.resume,
            key="bluesky_resume",
            name="Bluesky Resume",
            finished_slot=self._on_resume_finished,
            except_slot=self._on_resume_error,
        )
        self._resume_future.start()
        self.sigResume.emit()
        self._set_state(EngineState.RUNNING)

    def _on_resume_finished(self) -> None:
        """Called when resume completes successfully."""
        self.sigFinish.emit()
        if self._RE:
            self._set_state(_RE_STATE_MAP.get(self._RE.state, EngineState.IDLE))

    def _on_resume_error(self, ex: Exception) -> None:
        """Called when resume fails."""
        self.sigException.emit(ex)
        if self._RE:
            self._set_state(_RE_STATE_MAP.get(self._RE.state, EngineState.ERROR))

    def _interrupt_from_paused(
        self,
        method: Callable[..., Any],
        name: str,
        pending_state: EngineState,
        **kwargs: Any,
    ) -> None:
        """Dispatch a stop/abort/halt on a paused RunEngine off the caller's thread.

        From "paused", bluesky's RunEngine.stop()/abort()/halt() block the
        calling thread until the plan task fully completes cleanup
        (RunEngine._resume_task) — unbounded with real hardware, and every
        caller here is on the GUI thread. Same treatment as resume().
        """
        if self._interrupt_future is not None and self._interrupt_future.isRunning():
            # A second dispatch would make ThreadManager cancel (eventually
            # terminate) the thread hosting the in-flight plan cleanup.
            logger.debug("[bluesky] Interrupt already in flight; not dispatching another")
            return

        self._interrupt_future = QThreadFuture(
            method,
            key="bluesky_interrupt",
            name=name,
            finished_slot=self._on_interrupt_finished,
            except_slot=self._on_interrupt_error,
            **kwargs,
        )
        self._interrupt_future.start()
        self._set_state(pending_state)

    def _on_interrupt_finished(self) -> None:
        """Called when a stop/abort/halt dispatched from paused completes."""
        if self._RE:
            self._set_state(_RE_STATE_MAP.get(self._RE.state, EngineState.IDLE))

    def _on_interrupt_error(self, ex: Exception) -> None:
        """Called when a stop/abort/halt dispatched from paused fails."""
        self.sigException.emit(ex)
        if self._RE:
            self._set_state(_RE_STATE_MAP.get(self._RE.state, EngineState.ERROR))

    def stop(self) -> bool:
        """Stop the current plan gracefully (at next checkpoint).

        Bluesky's RunEngine.stop() supports both the running and paused
        states; gating on "running" alone made Stop a silent no-op on a
        paused run. From "paused" the call blocks until plan cleanup
        completes, so it is dispatched off-thread.

        Returns:
            True if a stop was actually dispatched to the RunEngine.
        """
        if self._RE is None:
            return False

        state = self._RE.state
        if state not in ("running", "paused"):
            return False

        logger.info("[bluesky] Stopping plan at next checkpoint")
        if state == "paused":
            self._interrupt_from_paused(
                self._RE.stop, name="Bluesky Stop", pending_state=EngineState.STOPPING
            )
        else:
            self._RE.stop()
            self._set_state(_RE_STATE_MAP.get(self._RE.state, EngineState.IDLE))
        return True

    def abort(self, reason: str = "") -> bool:
        """Abort the currently running or paused plan.

        From "paused" the call blocks until plan cleanup completes, so it
        is dispatched off-thread.

        Args:
            reason: Optional reason for the abort.

        Returns:
            True if an abort was actually dispatched to the RunEngine.
        """
        if self._RE is None:
            return False

        state = self._RE.state
        if state not in ("running", "paused"):
            return False

        logger.info(f"[bluesky] Aborting plan: {reason or 'No reason given'}")
        if state == "paused":
            self._interrupt_from_paused(
                self._RE.abort,
                name="Bluesky Abort",
                pending_state=EngineState.ABORTING,
                reason=reason,
            )
        else:
            self._RE.abort(reason=reason)
            self._set_state(_RE_STATE_MAP.get(self._RE.state, EngineState.ABORTING))
        self.sigAbort.emit()
        return True

    def halt(self) -> bool:
        """Halt the current plan immediately (emergency stop).

        From "paused" the call blocks until the plan task unwinds, so it
        is dispatched off-thread.

        Returns:
            True if a halt was actually dispatched to the RunEngine.
        """
        if self._RE is None:
            return False

        state = self._RE.state
        if state not in ("running", "paused"):
            return False

        logger.warning("[bluesky] Halting plan immediately")
        if state == "paused":
            self._interrupt_from_paused(
                self._RE.halt, name="Bluesky Halt", pending_state=EngineState.ABORTING
            )
        else:
            self._RE.halt()
            self._set_state(_RE_STATE_MAP.get(self._RE.state, EngineState.ABORTING))
        self.sigAbort.emit()
        return True

    # === Bluesky-Specific Methods ===

    def subscribe_kwargs_callable(self, callable_: Callable[[], dict[str, Any]]) -> None:
        """Register a callable that provides metadata for each plan.

        The callable will be invoked before each plan execution, and its
        return value (a dict) will be merged into the plan's kwargs.

        Args:
            callable_: A no-argument function returning a dict of metadata.
        """
        self._kwargs_callables.add(callable_)

    def unsubscribe_kwargs_callable(
        self, callable_: Callable[[], dict[str, Any]]
    ) -> None:
        """Remove a kwargs callable.

        Args:
            callable_: The callable to remove.
        """
        self._kwargs_callables.discard(callable_)

    def _check_if_ready(self, *args: Any) -> None:
        """Check if the queue is empty and RE is idle, emit sigReady if so."""
        if self._RE and self._RE.state == "idle" and self.queue_size == 0:
            self.sigReady.emit()
