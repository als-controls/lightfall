"""Qt-integrated Bluesky RunEngine for NCS.

Provides a QRunEngine class that wraps Bluesky's RunEngine with Qt signals
for UI integration, priority-based plan queuing, and proper threading.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from queue import Empty, PriorityQueue
from typing import TYPE_CHECKING, Any, Callable

from bluesky import RunEngine
from bluesky.utils import DuringTask, RunEngineInterrupted
from PySide6.QtCore import QObject, Signal

from ncs.utils.logging import logger
from ncs.utils.threads import QThreadFuture, method

if TYPE_CHECKING:
    from bluesky.utils import Msg

__all__ = ["QRunEngine", "get_run_engine"]


@dataclass(order=True)
class _PrioritizedPlan:
    """A plan with priority for queue ordering."""

    priority: int
    args: tuple[Any, ...] = field(compare=False)
    kwargs: dict[str, Any] = field(compare=False)


class QRunEngine(QObject):
    """Qt-integrated Bluesky RunEngine with priority queue and signals.

    This class wraps Bluesky's RunEngine to provide:
    - Background thread execution with dedicated asyncio event loop
    - Priority-based plan queuing
    - Qt signals for all state changes
    - Document streaming via signals

    Signals:
        sigDocumentYield(str, dict): Emitted for each document (name, doc).
        sigStart(): Emitted when a plan starts executing.
        sigFinish(): Emitted when a plan finishes successfully.
        sigPause(): Emitted when execution is paused.
        sigResume(): Emitted when execution resumes.
        sigAbort(): Emitted when execution is aborted.
        sigException(Exception): Emitted when an error occurs.
        sigReady(): Emitted when queue is empty and RE is idle.
        sigStateChanged(str): Emitted on any state change.

    Example:
        re = get_run_engine()
        re.sigDocumentYield.connect(handle_document)
        re.sigFinish.connect(on_scan_complete)

        # Queue a scan
        from bluesky.plans import scan
        re(scan([detector], motor, 0, 10, 11))

        # Or with priority (lower = higher priority)
        re.put(scan([detector], motor, 0, 10, 11), priority=0)
    """

    sigDocumentYield = Signal(str, dict)
    sigStart = Signal()
    sigFinish = Signal()
    sigPause = Signal()
    sigResume = Signal()
    sigAbort = Signal()
    sigException = Signal(Exception)
    sigReady = Signal()
    sigStateChanged = Signal(str)

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the QRunEngine.

        Args:
            **kwargs: Additional arguments passed to Bluesky's RunEngine.
        """
        super().__init__()

        self._RE: RunEngine | None = None
        self._re_kwargs = kwargs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: PriorityQueue[_PrioritizedPlan] = PriorityQueue()
        self._kwargs_callables: set[Callable[[], dict[str, Any]]] = set()
        self._resume_future: QThreadFuture | None = None

        # Connect internal signals
        self.sigFinish.connect(self._check_if_ready)
        self.sigAbort.connect(self._check_if_ready)
        self.sigException.connect(self._check_if_ready)

        # Start the queue processing thread
        self._start_queue_processor()

    @property
    def RE(self) -> RunEngine | None:
        """Access the underlying Bluesky RunEngine."""
        return self._RE

    @property
    def state(self) -> str:
        """Current state of the RunEngine ('idle', 'running', 'paused', or 'unknown')."""
        if self._RE is None:
            return "unknown"
        return self._RE.state

    @property
    def is_idle(self) -> bool:
        """Check if the RunEngine is idle."""
        return self._RE is not None and self._RE.state == "idle"

    def _start_queue_processor(self) -> None:
        """Start the background thread that processes the plan queue."""
        self._queue_future = QThreadFuture(
            self._process_queue,
            key="run_engine",
            name="RunEngine Queue Processor",
        )
        self._queue_future.start()

    def _process_queue(self) -> None:
        """Background thread loop that processes queued plans.

        Creates its own asyncio event loop and RunEngine instance.
        Runs indefinitely, processing plans from the priority queue.
        """
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

        # Subscribe to document stream
        self._RE.subscribe(lambda name, doc: self.sigDocumentYield.emit(name, doc))

        logger.info("RunEngine initialized and ready")
        self.sigStateChanged.emit("idle")

        # Main processing loop
        while True:
            try:
                plan = self._queue.get(block=True, timeout=0.1)
            except Empty:
                continue

            self._execute_plan(plan)
            self._queue.task_done()

    def _execute_plan(self, plan: _PrioritizedPlan) -> None:
        """Execute a single plan from the queue."""
        args, kwargs = plan.args, plan.kwargs

        # Inject metadata from registered callables
        for kwargs_callable in self._kwargs_callables:
            try:
                kwargs.update(kwargs_callable())
            except Exception as ex:
                logger.warning(f"Error in kwargs callable: {ex}")

        self.sigStart.emit()
        self.sigStateChanged.emit("running")
        logger.debug(f"Starting plan with args={args}, kwargs={kwargs}")

        try:
            self._RE(*args, **kwargs)
        except RunEngineInterrupted:
            logger.info("Plan was interrupted by user")
            self.sigAbort.emit()
        except Exception as ex:
            logger.error(f"Error during plan execution: {ex}")
            logger.exception(ex)
            self.sigException.emit(ex)
        else:
            self.sigFinish.emit()
        finally:
            self.sigStateChanged.emit(self._RE.state if self._RE else "unknown")

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Queue a plan for execution. Alias for put()."""
        self.put(*args, **kwargs)

    def put(self, *args: Any, priority: int = 1, **kwargs: Any) -> None:
        """Add a plan to the execution queue.

        Args:
            *args: Plan and its arguments.
            priority: Queue priority (lower number = higher priority).
            **kwargs: Additional keyword arguments for the plan.
        """
        self._queue.put(_PrioritizedPlan(priority, args, kwargs))
        logger.debug(f"Queued plan with priority {priority}")

    def abort(self, reason: str = "") -> None:
        """Abort the currently running plan.

        Args:
            reason: Optional reason for the abort.
        """
        if self._RE is None:
            return

        if self._RE.state == "running":
            logger.info(f"Aborting plan: {reason or 'No reason given'}")
            self._RE.abort(reason=reason)
            self.sigAbort.emit()
            self.sigStateChanged.emit(self._RE.state)

    def pause(self, defer: bool = False) -> None:
        """Pause the currently running plan.

        Args:
            defer: If True, pause at next checkpoint. If False, pause immediately.
        """
        if self._RE is None:
            return

        if self._RE.state != "paused":
            logger.info(f"Pausing plan (defer={defer})")
            self._RE.request_pause(defer)
            self.sigPause.emit()
            self.sigStateChanged.emit("pausing")

    def resume(self) -> None:
        """Resume a paused plan."""
        if self._RE is None or self._RE.state != "paused":
            return

        logger.info("Resuming plan")

        # Run resume in a separate thread to not block
        self._resume_future = QThreadFuture(
            self._RE.resume,
            key="run_engine_resume",
            name="RunEngine Resume",
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
            logger.info("Stopping plan at next checkpoint")
            self._RE.stop()

    def halt(self) -> None:
        """Halt the current plan immediately (emergency stop)."""
        if self._RE is None:
            return

        if self._RE.state in ("running", "paused"):
            logger.warning("Halting plan immediately")
            self._RE.halt()
            self.sigAbort.emit()
            self.sigStateChanged.emit(self._RE.state)

    def subscribe(self, callback: Callable[[str, dict], Any]) -> int:
        """Subscribe to the document stream.

        Args:
            callback: Function called with (name, document) for each document.

        Returns:
            Subscription token for unsubscribing.
        """
        if self._RE is None:
            raise RuntimeError("RunEngine not yet initialized")
        return self._RE.subscribe(callback)

    def unsubscribe(self, token: int) -> None:
        """Remove a document subscription.

        Args:
            token: The token returned by subscribe().
        """
        if self._RE is not None:
            self._RE.unsubscribe(token)

    def subscribe_kwargs_callable(self, callable_: Callable[[], dict[str, Any]]) -> None:
        """Register a callable that provides metadata for each plan.

        The callable will be invoked before each plan execution, and its
        return value (a dict) will be merged into the plan's kwargs.

        Args:
            callable_: A no-argument function returning a dict of metadata.
        """
        self._kwargs_callables.add(callable_)

    def unsubscribe_kwargs_callable(self, callable_: Callable[[], dict[str, Any]]) -> None:
        """Remove a kwargs callable.

        Args:
            callable_: The callable to remove.
        """
        self._kwargs_callables.discard(callable_)

    def _check_if_ready(self, *args: Any) -> None:
        """Check if the queue is empty and RE is idle, emit sigReady if so."""
        if self._RE and self._RE.state == "idle" and self._queue.empty():
            self.sigReady.emit()

    def clear_queue(self) -> int:
        """Clear all pending plans from the queue.

        Returns:
            Number of plans that were cleared.
        """
        count = 0
        while True:
            try:
                self._queue.get_nowait()
                count += 1
            except Empty:
                break
        logger.info(f"Cleared {count} plans from queue")
        return count

    @property
    def queue_size(self) -> int:
        """Number of plans waiting in the queue."""
        return self._queue.qsize()


# Module-level singleton
_run_engine: QRunEngine | None = None


def get_run_engine(**kwargs: Any) -> QRunEngine:
    """Get the global QRunEngine instance.

    Creates the instance on first call. Subsequent calls return the same instance.

    Args:
        **kwargs: Arguments passed to QRunEngine on first initialization.

    Returns:
        The global QRunEngine instance.
    """
    global _run_engine
    if _run_engine is None:
        _run_engine = QRunEngine(**kwargs)
        logger.debug("Created global QRunEngine instance")
    return _run_engine
