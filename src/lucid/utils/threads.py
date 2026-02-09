"""Threading utilities for NCS.

Provides Qt-integrated threading with global thread management, flexible decorators,
and proper interruption/cancellation support.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Generator
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "ThreadManager",
    "get_thread_manager",
    "thread_manager",
    "QThreadFuture",
    "QThreadFutureIterator",
    "method",
    "iterator",
    "invoke_in_main_thread",
    "invoke_as_event",
    "is_main_thread",
    "initialize_main_thread_invoker",
]

T = TypeVar("T")

# -----------------------------------------------------------------------------
# Coverage workaround for QThread tracing
# -----------------------------------------------------------------------------
_running_coverage = "coverage" in sys.modules


def _coverage_resolve_trace(fn: Callable[..., T]) -> Callable[..., T]:
    """Decorator to fix coverage tracing inside QThread.run() methods."""
    if not _running_coverage:
        return fn

    @wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> T:
        sys.settrace(threading._trace_hook)
        return fn(*args, **kwargs)

    return wrapped


# -----------------------------------------------------------------------------
# Thread Manager (Singleton)
# -----------------------------------------------------------------------------
class ThreadManager:
    """Global registry for tracking and managing QThreadFuture instances.

    Access via get_thread_manager() or the module-level thread_manager variable.
    """

    _instance: ThreadManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> ThreadManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        # Strong references to prevent GC before thread completion.
        # Threads unregister themselves in finally block when done.
        self._threads: dict[int, QThreadFuture] = {}
        self._keys: dict[str, int] = {}  # key -> thread id mapping
        self._registry_lock = threading.Lock()
        self._shutdown_connected = False
        self._connect_app_shutdown()

    def _connect_app_shutdown(self) -> None:
        """Connect to application shutdown signal if app exists."""
        if self._shutdown_connected:
            return
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self.shutdown)
            self._shutdown_connected = True

    def register(self, thread: QThreadFuture, key: str | None = None) -> None:
        """Register a thread for tracking.

        Holds a strong reference to prevent GC before thread completion.
        Threads must call unregister() when done (handled automatically
        in QThreadFuture.run() finally block).

        Args:
            thread: The QThreadFuture to track.
            key: Optional unique key for later retrieval. If a thread with
                this key already exists, the old one is cancelled first.
        """
        # Ensure shutdown is connected (in case app was created after ThreadManager)
        self._connect_app_shutdown()

        thread_id = id(thread)

        with self._registry_lock:
            # Cancel existing thread with same key
            if key and key in self._keys:
                old_id = self._keys[key]
                old_thread = self._threads.get(old_id)
                if old_thread and old_thread.isRunning():
                    logger.debug(f"Cancelling existing thread with key '{key}'")
                    old_thread.cancel()

            self._threads[thread_id] = thread
            if key:
                self._keys[key] = thread_id
                thread._manager_key = key

        logger.trace(f"Registered thread {thread_id}" + (f" with key '{key}'" if key else ""))

    def unregister(self, thread: QThreadFuture) -> None:
        """Unregister a thread from tracking."""
        thread_id = id(thread)
        with self._registry_lock:
            self._threads.pop(thread_id, None)
            key = getattr(thread, "_manager_key", None)
            if key and key in self._keys:
                del self._keys[key]
        logger.trace(f"Unregistered thread {thread_id}")

    def get_active(self) -> list[QThreadFuture]:
        """Get all currently active (running) threads."""
        active = []
        with self._registry_lock:
            for thread in list(self._threads.values()):
                if thread.isRunning():
                    active.append(thread)
        return active

    def get_by_key(self, key: str) -> QThreadFuture | None:
        """Get a thread by its key."""
        with self._registry_lock:
            thread_id = self._keys.get(key)
            if thread_id is None:
                return None
            return self._threads.get(thread_id)

    def cancel(self, key: str, timeout_ms: int = 5000) -> bool:
        """Cancel a thread by key.

        Args:
            key: The thread key.
            timeout_ms: Time to wait for graceful shutdown before force-terminating.

        Returns:
            True if thread was found and cancelled, False if not found.
        """
        thread = self.get_by_key(key)
        if thread is None:
            return False
        thread.cancel(timeout_ms=timeout_ms)
        return True

    def cancel_all(self, timeout_ms: int = 5000) -> None:
        """Cancel all active threads.

        Args:
            timeout_ms: Time to wait for each thread's graceful shutdown.
        """
        active = self.get_active()
        logger.debug(f"Cancelling {len(active)} active thread(s)")
        for thread in active:
            thread.cancel(timeout_ms=timeout_ms)

    def wait_all(self, timeout_ms: int | None = None) -> bool:
        """Wait for all active threads to complete.

        Args:
            timeout_ms: Maximum time to wait in milliseconds. None for indefinite.

        Returns:
            True if all threads finished, False if timeout occurred.
        """
        active = self.get_active()
        if not active:
            return True

        deadline = None
        if timeout_ms is not None:
            deadline = time.monotonic() + timeout_ms / 1000

        for thread in active:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                if not thread.wait(int(remaining * 1000)):
                    return False
            else:
                thread.wait()

        return True

    def shutdown(self) -> None:
        """Shutdown all threads. Called automatically on application quit."""
        logger.debug("ThreadManager shutting down")
        self.cancel_all(timeout_ms=3000)


def get_thread_manager() -> ThreadManager:
    """Get the global ThreadManager instance."""
    return ThreadManager()


# Module-level singleton access
thread_manager = get_thread_manager()


# -----------------------------------------------------------------------------
# QThreadFuture
# -----------------------------------------------------------------------------
class QThreadFuture(QThread):
    """A future-like QThread with automatic registration and improved cancellation.

    Uses Qt signals for cross-thread callback delivery, which is more robust
    than invoke_in_main_thread() as it doesn't require careful initialization
    timing of the invoker object.

    Signals:
        sigResult: Emitted with the return value when method completes.
        sigError: Emitted with the exception when an error occurs.
        sigDone: Emitted when thread finishes successfully (after sigResult).

    Example:
        def long_task(x):
            time.sleep(1)
            return x * 2

        future = QThreadFuture(long_task, 5, callback_slot=print)
        future.start()
        # Later: print receives 10
    """

    # Signals for cross-thread callback delivery
    # Qt handles thread marshalling automatically when these are emitted
    sigResult = Signal(object)  # Emitted with return value
    sigError = Signal(object)   # Emitted with exception
    sigDone = Signal()          # Emitted when finished successfully

    def __init__(
        self,
        method: Callable[..., Any],
        *args: Any,
        callback_slot: Callable[..., Any] | None = None,
        finished_slot: Callable[[], None] | None = None,
        except_slot: Callable[[Exception], None] | None = None,
        interrupt_callable: Callable[[], None] | None = None,
        priority: QThread.Priority = QThread.Priority.InheritPriority,
        timeout: int = 0,
        key: str | None = None,
        name: str | None = None,
        register: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize the thread future.

        Args:
            method: The callable to run in the background.
            *args: Positional arguments for the method.
            callback_slot: Called with the return value(s) when method completes.
            finished_slot: Called (no args) when thread finishes successfully.
            except_slot: Called with exception if an error occurs.
            interrupt_callable: Called when interrupt is requested (for custom cleanup).
            priority: Thread priority.
            timeout: Auto-cancel after this many milliseconds (0 = no timeout).
            key: Unique key for ThreadManager lookup. Threads with duplicate keys
                will cancel the previous thread.
            name: Name for the thread (for debugging).
            register: Whether to register with ThreadManager (default True).
            **kwargs: Keyword arguments for the method.
        """
        super().__init__()

        self._method = method
        self._args = args
        self._kwargs = kwargs
        self._callback_slot = callback_slot
        self._finished_slot = finished_slot
        self._except_slot = except_slot
        self._interrupt_callable = interrupt_callable
        self._priority = priority
        self._timeout = timeout
        self._key = key
        self._name = name or getattr(method, "__name__", "anonymous")
        self._register = register

        self._cancelled = False
        self._exception: Exception | None = None
        self._result: Any = None
        self._manager_key: str | None = None

        # Connect user-provided slots to signals
        # Qt's signal/slot mechanism handles cross-thread marshalling automatically
        if callback_slot:
            self.sigResult.connect(callback_slot)
        if except_slot:
            self.sigError.connect(except_slot)
        if finished_slot:
            self.sigDone.connect(finished_slot)

    @property
    def cancelled(self) -> bool:
        """Whether the thread was cancelled."""
        return self._cancelled

    @property
    def exception(self) -> Exception | None:
        """The exception raised during execution, if any."""
        return self._exception

    @property
    def done(self) -> bool:
        """Whether the thread has finished."""
        return self.isFinished()

    @property
    def running(self) -> bool:
        """Whether the thread is currently running."""
        return self.isRunning()

    def start(self) -> None:
        """Start the thread."""
        if self.running:
            raise RuntimeError("Thread is already running")

        if self._register:
            thread_manager.register(self, self._key)

        super().start(self._priority)

        if self._timeout > 0:
            QTimer.singleShot(self._timeout, self.cancel)

    @_coverage_resolve_trace
    def run(self) -> None:
        """Execute the method. Do not call directly; use start()."""
        threading.current_thread().name = self._name
        self._cancelled = False
        self._exception = None

        try:
            runner = self._run()
            while not self.isInterruptionRequested():
                try:
                    value = next(runner)
                except StopIteration as ex:
                    value = ex.value
                    self._result = value
                    if self._callback_slot:
                        self.sigResult.emit(value)
                    break

                # For regular QThreadFuture, emit result on each yield
                if not isinstance(self, QThreadFutureIterator) and self._callback_slot:
                    self.sigResult.emit(value)

        except Exception as ex:
            self._exception = ex
            logger.error(
                f"Error in thread '{self._name}': {ex}\n"
                f"Method: {getattr(self._method, '__name__', 'UNKNOWN')}\n"
                f"Args: {self._args}\n"
                f"Kwargs: {self._kwargs}"
            )
            logger.exception(ex)
            if self._except_slot:
                self.sigError.emit(ex)
        else:
            if self._finished_slot:
                self.sigDone.emit()
        finally:
            if self._register:
                thread_manager.unregister(self)

    def _run(self) -> Generator[Any, None, Any]:
        """Internal run implementation. Override in subclasses.

        For regular QThreadFuture, returns the method result via StopIteration
        so the callback is only invoked once. QThreadFutureIterator overrides
        this to yield intermediate values.
        """
        return self._method(*self._args, **self._kwargs)
        yield  # Makes this a generator function (never reached)

    def result(self, timeout_ms: int | None = None) -> Any:
        """Wait for and return the result.

        Args:
            timeout_ms: Maximum time to wait. None for indefinite.

        Returns:
            The return value of the method, or the exception if one occurred.

        Raises:
            TimeoutError: If timeout is exceeded.
        """
        if not self.running and not self.done:
            self.start()

        if timeout_ms is not None:
            if not self.wait(timeout_ms):
                raise TimeoutError(f"Thread did not complete within {timeout_ms}ms")
        else:
            self.wait()

        if self._exception:
            raise self._exception
        return self._result

    def cancel(self, timeout_ms: int = 5000) -> bool:
        """Cancel the thread.

        Args:
            timeout_ms: Time to wait for graceful shutdown before force-terminating.

        Returns:
            True if thread stopped, False if it had to be force-terminated.
        """
        if not self.running:
            return True

        self._cancelled = True
        self.requestInterruption()

        if self._interrupt_callable:
            try:
                self._interrupt_callable()
            except Exception as ex:
                logger.warning(f"Error in interrupt callable: {ex}")

        # Wait for graceful shutdown
        if self.wait(timeout_ms):
            logger.debug(f"Thread '{self._name}' stopped gracefully")
            return True

        # Force terminate
        logger.warning(f"Thread '{self._name}' did not respond to interrupt, force terminating")
        self.terminate()
        self.wait(1000)  # Brief wait after terminate
        return False

    def interrupt(self) -> None:
        """Request interruption without waiting."""
        self.requestInterruption()
        if self._interrupt_callable:
            self._interrupt_callable()

    def __enter__(self) -> QThreadFuture:
        """Context manager entry - starts the thread."""
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - waits for completion."""
        self.wait()


# -----------------------------------------------------------------------------
# QThreadFutureIterator
# -----------------------------------------------------------------------------
class QThreadFutureIterator(QThreadFuture):
    """QThreadFuture variant for generators that yields intermediate values.

    The yield_slot is called for each yielded value, while callback_slot
    is called with the final return value.

    Signals:
        sigYield: Emitted with each yielded value from the generator.
    """

    # Signal for yielded values (separate from sigResult which is for final value)
    sigYield = Signal(object)

    def __init__(
        self,
        method: Callable[..., Generator[Any, None, Any]],
        *args: Any,
        yield_slot: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the iterator thread.

        Args:
            method: A generator function.
            *args: Positional arguments for the generator.
            yield_slot: Called with each yielded value.
            **kwargs: Additional arguments (see QThreadFuture).
        """
        super().__init__(method, *args, **kwargs)
        self._yield_slot = yield_slot

        # Connect yield_slot to sigYield signal
        if yield_slot:
            self.sigYield.connect(yield_slot)

    def _run(self) -> Generator[Any, None, Any]:
        """Run the generator, yielding each value."""
        gen = self._method(*self._args, **self._kwargs)
        for value in gen:
            if self.isInterruptionRequested():
                return
            if self._yield_slot:
                self.sigYield.emit(value)
            yield value


# -----------------------------------------------------------------------------
# Main Thread Invocation
# -----------------------------------------------------------------------------
# Lazy-initialized to avoid creating Qt objects before QApplication exists.
# On Windows, creating QObjects before QApplication can cause crashes.
_invoke_event_type: QEvent.Type | None = None
_invoker: _Invoker | None = None
_invoker_lock = threading.Lock()


def _get_invoke_event_type() -> QEvent.Type:
    """Get or create the custom event type (lazy initialization)."""
    global _invoke_event_type
    if _invoke_event_type is None:
        _invoke_event_type = QEvent.Type(QEvent.registerEventType())
    return _invoke_event_type


def _get_invoker() -> _Invoker:
    """Get or create the invoker singleton (lazy initialization)."""
    global _invoker
    if _invoker is None:
        with _invoker_lock:
            if _invoker is None:
                _invoker = _Invoker()
    return _invoker


def initialize_main_thread_invoker() -> None:
    """Initialize the invoker on the main thread.

    Call this after QApplication is created but before starting any
    background threads that use invoke_in_main_thread().

    This is required because the invoker (a QObject) must be created
    on the main thread for proper event delivery.
    """
    if not is_main_thread():
        raise RuntimeError("initialize_main_thread_invoker must be called from main thread")
    _get_invoker()
    _get_invoke_event_type()


class _InvokeEvent(QEvent):
    """QEvent that carries a callable for main thread execution."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__(_get_invoke_event_type())
        self.fn = fn
        self.args = args
        self.kwargs = kwargs


class _Invoker(QObject):
    """QObject that processes InvokeEvents in the main thread."""

    def event(self, event: QEvent) -> bool:
        if isinstance(event, _InvokeEvent):
            try:
                # Check if it's a signal (has emit method)
                if hasattr(event.fn, "emit"):
                    event.fn.emit(*event.args)
                else:
                    event.fn(*event.args, **event.kwargs)
            except Exception as ex:
                logger.error(f"Error invoking callback in main thread: {ex}")
                logger.exception(ex)
            return True
        return super().event(event)


def invoke_in_main_thread(fn: Callable[..., Any], *args: Any, force_event: bool = False, **kwargs: Any) -> None:
    """Invoke a callable in the main thread.

    If already in the main thread and force_event is False, calls immediately.
    Otherwise posts an event to be processed in the main thread's event loop.

    Args:
        fn: The callable to invoke.
        *args: Positional arguments.
        force_event: If True, always post as event even if in main thread.
        **kwargs: Keyword arguments.
    """
    if not force_event and is_main_thread():
        fn(*args, **kwargs)
    else:
        QCoreApplication.postEvent(_get_invoker(), _InvokeEvent(fn, *args, **kwargs))


def invoke_as_event(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Invoke a callable as an event in the main thread (always posts event)."""
    invoke_in_main_thread(fn, *args, force_event=True, **kwargs)


def is_main_thread() -> bool:
    """Check if the current thread is the main thread."""
    return threading.current_thread() is threading.main_thread()


# -----------------------------------------------------------------------------
# Decorators
# -----------------------------------------------------------------------------
def method(
    callback_slot: Callable[..., Any] | None = None,
    finished_slot: Callable[[], None] | None = None,
    except_slot: Callable[[Exception], None] | None = None,
    priority: QThread.Priority = QThread.Priority.InheritPriority,
    timeout: int = 0,
    block: bool = False,
    key: str | None = None,
    name: str | None = None,
) -> Callable[[Callable[..., T]], Callable[..., QThreadFuture]]:
    """Decorator to run a function on a background thread.

    The decorated function returns a QThreadFuture that starts immediately.
    Callback slots can be overridden at call time using underscore-prefixed kwargs:
    _callback_slot, _finished_slot, _except_slot.

    Args:
        callback_slot: Default callback for return value.
        finished_slot: Default slot called on completion.
        except_slot: Default slot called on exception.
        priority: Thread priority.
        timeout: Auto-cancel timeout in milliseconds.
        block: If True, wait for result before returning.
        key: Thread key for ThreadManager.
        name: Thread name for debugging.

    Example:
        @threads.method(callback_slot=handle_result)
        def compute(x):
            return x * 2

        # Use default callback:
        future = compute(5)

        # Override callback at call time:
        future = compute(5, _callback_slot=other_handler)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., QThreadFuture]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> QThreadFuture:
            # Extract override kwargs
            cb = kwargs.pop("_callback_slot", callback_slot)
            fs = kwargs.pop("_finished_slot", finished_slot)
            es = kwargs.pop("_except_slot", except_slot)

            future = QThreadFuture(
                func,
                *args,
                callback_slot=cb,
                finished_slot=fs,
                except_slot=es,
                priority=priority,
                timeout=timeout,
                key=key,
                name=name or func.__name__,
                **kwargs,
            )
            future.start()

            if block:
                future.result()

            return future

        return wrapper

    return decorator


def iterator(
    yield_slot: Callable[..., Any] | None = None,
    callback_slot: Callable[..., Any] | None = None,
    finished_slot: Callable[[], None] | None = None,
    except_slot: Callable[[Exception], None] | None = None,
    priority: QThread.Priority = QThread.Priority.InheritPriority,
    key: str | None = None,
    name: str | None = None,
) -> Callable[[Callable[..., Generator[Any, None, T]]], Callable[..., QThreadFutureIterator]]:
    """Decorator to run a generator on a background thread.

    Each yielded value is passed to yield_slot. The final return value
    goes to callback_slot. Slots can be overridden at call time using
    underscore-prefixed kwargs.

    Args:
        yield_slot: Called with each yielded value.
        callback_slot: Called with the final return value.
        finished_slot: Called on completion.
        except_slot: Called on exception.
        priority: Thread priority.
        key: Thread key for ThreadManager.
        name: Thread name for debugging.

    Example:
        @threads.iterator(yield_slot=update_progress)
        def process_items(items):
            for i, item in enumerate(items):
                yield i / len(items)  # Progress
                process(item)
            return "done"

        future = process_items(my_items)
    """
    def decorator(func: Callable[..., Generator[Any, None, T]]) -> Callable[..., QThreadFutureIterator]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> QThreadFutureIterator:
            # Extract override kwargs
            ys = kwargs.pop("_yield_slot", yield_slot)
            cb = kwargs.pop("_callback_slot", callback_slot)
            fs = kwargs.pop("_finished_slot", finished_slot)
            es = kwargs.pop("_except_slot", except_slot)

            future = QThreadFutureIterator(
                func,
                *args,
                yield_slot=ys,
                callback_slot=cb,
                finished_slot=fs,
                except_slot=es,
                priority=priority,
                key=key,
                name=name or func.__name__,
                **kwargs,
            )
            future.start()

            return future

        return wrapper

    return decorator
