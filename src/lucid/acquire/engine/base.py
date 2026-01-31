"""Base engine implementation.

Provides a base class with common functionality for engines.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from queue import Empty, PriorityQueue
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from lucid.acquire.engine.state import EngineState
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.ui.toast import ToastManager


@dataclass(order=True)
class PrioritizedProcedure:
    """A procedure with priority for queue ordering.

    Attributes:
        priority: Queue priority (lower = higher priority).
        procedure: The procedure to execute (type is engine-specific).
        kwargs: Additional keyword arguments for the procedure.
        id: Unique identifier for this procedure (auto-generated UUID).
        submitted_at: Timestamp when the procedure was submitted.
        name: Human-readable name for the procedure (auto-detected or provided).
    """

    priority: int
    procedure: Any = field(compare=False)
    kwargs: dict[str, Any] = field(compare=False, default_factory=dict)
    id: str = field(compare=False, default_factory=lambda: str(uuid.uuid4()))
    submitted_at: datetime = field(compare=False, default_factory=datetime.now)
    name: str = field(compare=False, default="")


class BaseEngine(QObject):
    """Base class for execution engines.

    Provides common functionality:
    - Priority queue management
    - Subscription management
    - Qt signal declarations
    - State property implementation

    Subclasses must implement:
    - pause(): Pause execution
    - resume(): Resume paused execution
    - stop(): Graceful stop
    - abort(): Immediate abort
    - halt(): Emergency halt

    Signals:
        sigOutput(str, dict): Emitted for each (name, document) output pair.
        sigStart(): Emitted when a procedure starts executing.
        sigFinish(): Emitted when a procedure finishes successfully.
        sigPause(): Emitted when execution is paused.
        sigResume(): Emitted when execution resumes.
        sigAbort(): Emitted when execution is aborted.
        sigException(Exception): Emitted when an error occurs.
        sigReady(): Emitted when queue is empty and engine is idle.
        sigStateChanged(str): Emitted on any state change.
    """

    # Qt Signals
    sigOutput = Signal(str, dict)
    sigStart = Signal()
    sigFinish = Signal()
    sigPause = Signal()
    sigResume = Signal()
    sigAbort = Signal()
    sigException = Signal(Exception)
    sigReady = Signal()
    sigStateChanged = Signal(str)
    sigQueueChanged = Signal()  # Emitted when queue items are added/removed/reordered

    def __init__(
        self, name: str = "engine", *, toast_notifications: bool = True, **kwargs: Any
    ) -> None:
        """Initialize the base engine.

        Args:
            name: Human-readable engine identifier.
            toast_notifications: Whether to show toast notifications on run
                completion, abort, and errors. Defaults to True.
            **kwargs: Additional arguments (passed to QObject).
        """
        super().__init__()
        self._name = name
        self._state = EngineState.IDLE
        self._queue: PriorityQueue[PrioritizedProcedure] = PriorityQueue()
        self._queue_items: list[PrioritizedProcedure] = []  # Parallel list for management
        self._current_procedure: PrioritizedProcedure | None = None  # Currently running
        self._subscribers: dict[int, Callable[[str, dict], Any]] = {}
        self._next_token = 0
        self._toast_notifications = toast_notifications
        self._toast_manager: ToastManager | None = None

        # Connect state signals for ready check
        self.sigFinish.connect(self._check_if_ready)
        self.sigAbort.connect(self._check_if_ready)
        self.sigException.connect(self._check_if_ready)

        # Connect toast notification handlers
        self.sigFinish.connect(self._on_finish_toast)
        self.sigAbort.connect(self._on_abort_toast)
        self.sigException.connect(self._on_exception_toast)

    # === Properties ===

    @property
    def name(self) -> str:
        """Human-readable engine name."""
        return self._name

    @property
    def state(self) -> EngineState:
        """Current engine state."""
        return self._state

    @property
    def state_name(self) -> str:
        """Current state as lowercase string."""
        return str(self._state)

    @property
    def is_idle(self) -> bool:
        """Whether engine is idle and ready for new procedures."""
        return self._state == EngineState.IDLE

    @property
    def queue_size(self) -> int:
        """Number of procedures waiting in the queue."""
        return self._queue.qsize()

    @property
    def toast_notifications(self) -> bool:
        """Whether toast notifications are enabled for run events."""
        return self._toast_notifications

    @toast_notifications.setter
    def toast_notifications(self, value: bool) -> None:
        """Enable or disable toast notifications."""
        self._toast_notifications = value

    def _get_toast_manager(self) -> ToastManager:
        """Get the ToastManager instance (lazy initialization)."""
        if self._toast_manager is None:
            from lucid.ui.toast import ToastManager

            self._toast_manager = ToastManager.get_instance()
        return self._toast_manager

    # === Queue Operations ===

    def submit(
        self,
        procedure: Any,
        *,
        priority: int = 1,
        name: str = "",
        **kwargs: Any,
    ) -> str:
        """Submit a procedure for execution.

        Args:
            procedure: The procedure to execute.
            priority: Queue priority (lower = higher priority). Default is 1.
            name: Human-readable name for the procedure. If not provided,
                attempts to detect from generator function name.
            **kwargs: Additional procedure parameters.

        Returns:
            The unique ID of the submitted procedure.
        """
        # Auto-detect name from generator if not provided
        if not name:
            name = self._get_procedure_name(procedure)

        item = PrioritizedProcedure(priority, procedure, kwargs, name=name)
        self._queue.put(item)
        self._queue_items.append(item)
        self._queue_items.sort(key=lambda x: x.priority)
        logger.debug(f"[{self._name}] Queued '{name}' with priority {priority}, id={item.id[:8]}")
        self.sigQueueChanged.emit()
        return item.id

    def _get_procedure_name(self, procedure: Any) -> str:
        """Attempt to get a human-readable name for a procedure.

        Args:
            procedure: The procedure (generator, callable, etc.)

        Returns:
            A name string, or "procedure" if unable to determine.
        """
        # Check for generator's function name
        if hasattr(procedure, "gi_code"):
            return str(procedure.gi_code.co_name)
        # Check for callable's name
        if callable(procedure) and hasattr(procedure, "__name__"):
            return str(procedure.__name__)
        # Check for functools.partial
        if hasattr(procedure, "func") and hasattr(procedure.func, "__name__"):
            return str(procedure.func.__name__)
        return "procedure"

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Convenience method for submit().

        If a single positional argument is provided, it's used as the procedure.
        Otherwise, all args are bundled as the procedure.
        """
        if args:
            procedure = args[0] if len(args) == 1 else args
            self.submit(procedure, **kwargs)
        else:
            raise ValueError("No procedure provided")

    def clear_queue(self) -> int:
        """Clear all pending procedures from the queue.

        Returns:
            Number of procedures removed.
        """
        count = 0
        while True:
            try:
                self._queue.get_nowait()
                count += 1
            except Empty:
                break
        self._queue_items.clear()
        if count:
            logger.info(f"[{self._name}] Cleared {count} procedures from queue")
            self.sigQueueChanged.emit()
        return count

    def get_queue_items(self) -> list[PrioritizedProcedure]:
        """Get a copy of the current queue items.

        Returns:
            List of queued procedures sorted by priority.
        """
        return list(self._queue_items)

    def get_current_procedure(self) -> PrioritizedProcedure | None:
        """Get the currently running procedure, if any.

        Returns:
            The currently executing procedure, or None if idle.
        """
        return self._current_procedure

    def get_procedure_by_id(self, procedure_id: str) -> PrioritizedProcedure | None:
        """Get a queued procedure by its ID.

        Args:
            procedure_id: The procedure's unique ID.

        Returns:
            The procedure if found, None otherwise.
        """
        for item in self._queue_items:
            if item.id == procedure_id:
                return item
        return None

    def remove_from_queue(self, procedure_id: str) -> bool:
        """Remove a procedure from the queue by ID.

        Args:
            procedure_id: The ID of the procedure to remove.

        Returns:
            True if the procedure was found and removed.
        """
        # Find the item in our tracking list
        item_to_remove = None
        for item in self._queue_items:
            if item.id == procedure_id:
                item_to_remove = item
                break

        if item_to_remove is None:
            logger.warning(f"[{self._name}] Procedure {procedure_id[:8]} not found in queue")
            return False

        # Remove from tracking list
        self._queue_items.remove(item_to_remove)

        # Rebuild the priority queue without this item
        self._rebuild_priority_queue()

        logger.info(f"[{self._name}] Removed procedure {procedure_id[:8]} from queue")
        self.sigQueueChanged.emit()
        return True

    def update_priority(self, procedure_id: str, new_priority: int) -> bool:
        """Update the priority of a queued procedure.

        Args:
            procedure_id: The ID of the procedure to update.
            new_priority: The new priority value (lower = higher priority).

        Returns:
            True if the procedure was found and updated.
        """
        # Find the item in our tracking list
        for item in self._queue_items:
            if item.id == procedure_id:
                old_priority = item.priority
                # Create new item with updated priority (dataclass is frozen by order=True)
                # We need to modify the object directly since we're using it in a list
                object.__setattr__(item, "priority", new_priority)

                # Re-sort the tracking list
                self._queue_items.sort(key=lambda x: x.priority)

                # Rebuild the priority queue
                self._rebuild_priority_queue()

                logger.debug(
                    f"[{self._name}] Updated priority for {procedure_id[:8]}: "
                    f"{old_priority} -> {new_priority}"
                )
                self.sigQueueChanged.emit()
                return True

        logger.warning(f"[{self._name}] Procedure {procedure_id[:8]} not found in queue")
        return False

    def _rebuild_priority_queue(self) -> None:
        """Rebuild the PriorityQueue from the tracking list.

        Called after removing items or updating priorities.
        """
        # Drain the old queue
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break

        # Re-add all items
        for item in self._queue_items:
            self._queue.put(item)

    def _pop_next_procedure(self) -> PrioritizedProcedure | None:
        """Pop the next procedure from the queue.

        Also removes from tracking list and sets as current.
        Called by subclasses when starting execution.

        Returns:
            The next procedure to execute, or None if queue is empty.
        """
        try:
            item = self._queue.get_nowait()
            # Remove from tracking list
            if item in self._queue_items:
                self._queue_items.remove(item)
            self._current_procedure = item
            self.sigQueueChanged.emit()
            return item
        except Empty:
            return None

    def _clear_current_procedure(self) -> None:
        """Clear the current procedure reference.

        Called by subclasses when execution finishes.
        """
        self._current_procedure = None

    # === Subscription Management ===

    def subscribe(self, callback: Callable[[str, dict], Any]) -> int:
        """Subscribe to output stream.

        Args:
            callback: Function called with (name, document) for each output.

        Returns:
            Subscription token for unsubscribing.
        """
        token = self._next_token
        self._next_token += 1
        self._subscribers[token] = callback
        logger.debug(f"[{self._name}] Added subscriber with token {token}")
        return token

    def unsubscribe(self, token: int) -> None:
        """Remove an output subscription.

        Args:
            token: Token returned by subscribe().
        """
        if self._subscribers.pop(token, None) is not None:
            logger.debug(f"[{self._name}] Removed subscriber with token {token}")

    def _emit_output(self, name: str, doc: dict[str, Any]) -> None:
        """Emit output to signal and subscribers.

        Args:
            name: Output name (e.g., 'start', 'event', 'stop').
            doc: Output document dictionary.
        """
        # Emit Qt signal
        self.sigOutput.emit(name, doc)

        # Call direct subscribers
        for callback in self._subscribers.values():
            try:
                callback(name, doc)
            except Exception as ex:
                logger.warning(f"[{self._name}] Subscriber error: {ex}")

    # === State Management ===

    def _set_state(self, new_state: EngineState) -> None:
        """Update state and emit signal.

        Args:
            new_state: The new engine state.
        """
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            self.sigStateChanged.emit(str(new_state))
            logger.debug(f"[{self._name}] State: {old_state} -> {new_state}")

    def _check_if_ready(self, *args: Any) -> None:
        """Check if queue is empty and emit sigReady if so."""
        if self._state == EngineState.IDLE and self._queue.empty():
            self.sigReady.emit()

    # === Toast Notification Handlers ===

    def _on_finish_toast(self) -> None:
        """Show toast notification when a run finishes successfully."""
        if not self._toast_notifications:
            return
        try:
            toast = self._get_toast_manager()
            toast.success("Run Complete", f"{self._name}: Run finished successfully")
        except Exception as ex:
            logger.warning(f"[{self._name}] Failed to show finish toast: {ex}")

    def _on_abort_toast(self) -> None:
        """Show toast notification when a run is aborted."""
        if not self._toast_notifications:
            return
        try:
            toast = self._get_toast_manager()
            toast.warning("Run Aborted", f"{self._name}: Run was aborted")
        except Exception as ex:
            logger.warning(f"[{self._name}] Failed to show abort toast: {ex}")

    def _on_exception_toast(self, exception: Exception) -> None:
        """Show toast notification when a run encounters an error.

        Args:
            exception: The exception that occurred.
        """
        if not self._toast_notifications:
            return
        try:
            toast = self._get_toast_manager()
            error_msg = str(exception) if str(exception) else type(exception).__name__
            toast.error("Run Failed", f"{self._name}: {error_msg}")
        except Exception as ex:
            logger.warning(f"[{self._name}] Failed to show exception toast: {ex}")

    # === Methods to Override ===

    def pause(self, defer: bool = False) -> None:
        """Pause execution.

        Subclasses must override this method.

        Args:
            defer: If True, pause at next safe point. If False, pause immediately.
        """
        raise NotImplementedError("Subclasses must implement pause()")

    def resume(self) -> None:
        """Resume paused execution.

        Subclasses must override this method.
        """
        raise NotImplementedError("Subclasses must implement resume()")

    def stop(self) -> None:
        """Request graceful stop at next safe point.

        Subclasses must override this method.
        """
        raise NotImplementedError("Subclasses must implement stop()")

    def abort(self, reason: str = "") -> None:
        """Abort execution immediately.

        Subclasses must override this method.

        Args:
            reason: Optional reason for the abort.
        """
        raise NotImplementedError("Subclasses must implement abort()")

    def halt(self) -> None:
        """Emergency halt - immediately terminate execution.

        Subclasses must override this method.
        """
        raise NotImplementedError("Subclasses must implement halt()")
