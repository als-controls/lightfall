"""Engine protocol definition.

Defines the interface that all execution engines must satisfy.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lightfall.acquire.engine.state import EngineState


@runtime_checkable
class Engine(Protocol):
    """Protocol for execution engines.

    An Engine takes procedures from a queue and executes them, emitting
    (name, dict) output pairs and Qt signals for state changes.

    This protocol uses Qt signals for UI integration. Implementations
    must be QObject subclasses or provide compatible signal attributes.

    Required Qt Signals (documented here, cannot be enforced by Protocol):
        sigOutput(str, dict): Emitted for each (name, document) output pair.
        sigStart(): Emitted when a procedure starts executing.
        sigFinish(): Emitted when a procedure finishes successfully.
        sigPause(): Emitted when execution is paused.
        sigResume(): Emitted when execution resumes.
        sigAbort(): Emitted when execution is aborted.
        sigException(Exception): Emitted when an error occurs.
        sigReady(): Emitted when queue is empty and engine is idle.
        sigStateChanged(str): Emitted on any state change.

    Example:
        def handle_output(name: str, doc: dict) -> None:
            print(f"Received {name}: {doc}")

        engine = get_engine()
        engine.sigOutput.connect(handle_output)
        engine.submit(my_procedure)
    """

    # === Properties ===

    @property
    def state(self) -> EngineState:
        """Current engine state."""
        ...

    @property
    def state_name(self) -> str:
        """Current state as lowercase string (for Qt signal compatibility)."""
        ...

    @property
    def is_idle(self) -> bool:
        """Whether engine is idle and ready for new procedures."""
        ...

    @property
    def queue_size(self) -> int:
        """Number of procedures waiting in the queue."""
        ...

    @property
    def name(self) -> str:
        """Human-readable engine name/identifier."""
        ...

    # === Core Operations ===

    def submit(self, procedure: Any, *, priority: int = 1, **kwargs: Any) -> None:
        """Submit a procedure for execution.

        Args:
            procedure: The procedure to execute (type is implementation-specific).
            priority: Queue priority (lower = higher priority). Default is 1.
            **kwargs: Additional procedure parameters.
        """
        ...

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Convenience method for submit(). Alias for submit()."""
        ...

    # === Control Operations ===

    def pause(self, defer: bool = False) -> None:
        """Pause execution.

        Args:
            defer: If True, pause at next safe point. If False, pause immediately.
        """
        ...

    def resume(self) -> None:
        """Resume paused execution."""
        ...

    def stop(self) -> bool:
        """Request graceful stop at next safe point.

        Returns:
            True if a stop was actually dispatched.
        """
        ...

    def abort(self, reason: str = "") -> bool:
        """Abort execution immediately.

        Args:
            reason: Optional reason for the abort.

        Returns:
            True if an abort was actually dispatched.
        """
        ...

    def halt(self) -> bool:
        """Emergency halt - immediately terminate execution.

        Returns:
            True if a halt was actually dispatched.
        """
        ...

    def clear_queue(self) -> int:
        """Clear all pending procedures from the queue.

        Returns:
            Number of procedures removed.
        """
        ...

    # === Output Subscription ===

    def subscribe(self, callback: Callable[[str, dict[str, Any]], Any]) -> int:
        """Subscribe to output stream.

        Args:
            callback: Function called with (name, document) for each output.

        Returns:
            Subscription token for unsubscribing.
        """
        ...

    def unsubscribe(self, token: int) -> None:
        """Remove an output subscription.

        Args:
            token: Token returned by subscribe().
        """
        ...
