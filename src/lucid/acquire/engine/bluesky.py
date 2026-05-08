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

from lucid.acquire.engine.base import BaseEngine, PrioritizedProcedure
from lucid.acquire.engine.state import EngineState
from lucid.acquire.engine.waiting_hook import WaitingHookBridge
from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture

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
        self._re_kwargs = kwargs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._kwargs_callables: set[Callable[[], dict[str, Any]]] = set()
        self._resume_future: QThreadFuture | None = None
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

        Creates its own asyncio event loop and RunEngine instance.
        Runs indefinitely, processing plans from the priority queue.
        """
        from queue import Empty

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
        self.sigStateChanged.emit("idle")

        # Main processing loop - check for interruption request to allow clean shutdown
        while not QThread.currentThread().isInterruptionRequested():
            try:
                # Block on the priority queue with timeout
                item = self._queue.get(block=True, timeout=0.1)
            except Empty:
                continue

            # Track this item as current and remove from tracking list
            if item in self._queue_items:
                self._queue_items.remove(item)
            self._current_procedure = item
            self.sigQueueChanged.emit()

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

        self.sigStart.emit()
        self.sigStateChanged.emit("running")
        logger.debug(f"[bluesky] Starting plan '{item.name}' with kwargs={kwargs}")

        # RE is guaranteed to exist here - created in _process_queue before this method is called
        assert self._RE is not None

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
            self.sigFinish.emit()
        finally:
            self._clear_current_procedure()
            self.sigStateChanged.emit(self._RE.state if self._RE else "unknown")

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
            self.sigStateChanged.emit("pausing")

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
        self.sigStateChanged.emit("running")

    def _on_resume_finished(self) -> None:
        """Called when resume completes successfully."""
        self.sigFinish.emit()
        if self._RE:
            self.sigStateChanged.emit(self._RE.state)

    def _on_resume_error(self, ex: Exception) -> None:
        """Called when resume fails."""
        self.sigException.emit(ex)
        if self._RE:
            self.sigStateChanged.emit(self._RE.state)

    def stop(self) -> None:
        """Stop the current plan gracefully (at next checkpoint)."""
        if self._RE is None:
            return

        if self._RE.state == "running":
            logger.info("[bluesky] Stopping plan at next checkpoint")
            self._RE.stop()

    def abort(self, reason: str = "") -> None:
        """Abort the currently running plan.

        Args:
            reason: Optional reason for the abort.
        """
        if self._RE is None:
            return

        if self._RE.state == "running":
            logger.info(f"[bluesky] Aborting plan: {reason or 'No reason given'}")
            self._RE.abort(reason=reason)
            self.sigAbort.emit()
            self.sigStateChanged.emit(self._RE.state)

    def halt(self) -> None:
        """Halt the current plan immediately (emergency stop)."""
        if self._RE is None:
            return

        if self._RE.state in ("running", "paused"):
            logger.warning("[bluesky] Halting plan immediately")
            self._RE.halt()
            self.sigAbort.emit()
            self.sigStateChanged.emit(self._RE.state)

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
        if self._RE and self._RE.state == "idle" and self._queue.empty():
            self.sigReady.emit()
