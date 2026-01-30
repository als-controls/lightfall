"""Base engine implementation.

Provides a base class with common functionality for engines.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
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
    """

    priority: int
    procedure: Any = field(compare=False)
    kwargs: dict[str, Any] = field(compare=False, default_factory=dict)


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

    def submit(self, procedure: Any, *, priority: int = 1, **kwargs: Any) -> None:
        """Submit a procedure for execution.

        Args:
            procedure: The procedure to execute.
            priority: Queue priority (lower = higher priority). Default is 1.
            **kwargs: Additional procedure parameters.
        """
        self._queue.put(PrioritizedProcedure(priority, procedure, kwargs))
        logger.debug(f"[{self._name}] Queued procedure with priority {priority}")

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
        if count:
            logger.info(f"[{self._name}] Cleared {count} procedures from queue")
        return count

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
