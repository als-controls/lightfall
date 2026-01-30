"""Threading utilities for NCS.

Provides Qt-integrated threading with global thread management, flexible decorators,
and proper interruption/cancellation support.
"""

from __future__ import annotations

import sys
import threading
import time
import weakref
from collections.abc import Callable, Generator
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer
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
        self._threads: dict[int, weakref.ref[QThreadFuture]] = {}
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
                old_ref = self._threads.get(old_id)
                if old_ref:
                    old_thread = old_ref()
                    if old_thread and old_thread.isRunning():
                        logger.debug(f"Cancelling existing thread with key '{key}'")
                        old_thread.cancel()

            self._threads[thread_id] = weakref.ref(thread, self._make_cleanup(thread_id))
            if key:
                self._keys[key] = thread_id
                thread._manager_key = key

        logger.trace(f"Registered thread {thread_id}" + (f" with key '{key}'" if key else ""))

    def _make_cleanup(self, thread_id: int) -> Callable[[weakref.ref], None]:
        """Create a weak reference callback to clean up when thread is garbage collected."""
        def cleanup(ref: weakref.ref) -> None:
            with self._registry_lock:
                self._threads.pop(thread_id, None)
                # Clean up key mapping
                keys_to_remove = [k for k, v in self._keys.items() if v == thread_id]
                for k in keys_to_remove:
                    del self._keys[k]
        return cleanup

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
            for ref in list(self._threads.values()):
                thread = ref()
                if thread and thread.isRunning():
                    active.append(thread)
        return active

    def get_by_key(self, key: str) -> QThreadFuture | None:
        """Get a thread by its key."""
        with self._registry_lock:
            thread_id = self._keys.get(key)
            if thread_id is None:
                return None
            ref = self._threads.get(thread_id)
            if ref is None:
                return None
            return ref()

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

    Signals:
        sigFinished: Emitted when the thread completes successfully.
        sigExcept: Emitted with the exception when an error occurs.

    Example:
        def long_task(x):
            time.sleep(1)
            return x * 2

        future = QThreadFuture(long_task, 5, callback_slot=print)
        future.start()
        # Later: print receives 10
    """

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
                        self._invoke_callback(value)
                    break

                # For regular QThreadFuture, invoke callback on each yield
                if not isinstance(self, QThreadFutureIterator) and self._callback_slot:
                    self._invoke_callback(value)

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
                invoke_in_main_thread(self._except_slot, ex)
        else:
            if self._finished_slot:
                invoke_in_main_thread(self._finished_slot)
        finally:
            if self._register:
                thread_manager.unregister(self)

    def _run(self) -> Generator[Any, None, Any]:
        """Internal run implementation. Override in subclasses."""
        yield self._method(*self._args, **self._kwargs)

    def _invoke_callback(self, value: Any) -> None:
        """Invoke the callback with the given value."""
        if not isinstance(value, tuple):
            value = (value,)
        invoke_in_main_thread(self._callback_slot, *value)

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
    """

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

    def _run(self) -> Generator[Any, None, Any]:
        """Run the generator, yielding each value."""
        gen = self._method(*self._args, **self._kwargs)
        for value in gen:
            if self.isInterruptionRequested():
                return
            if self._yield_slot:
                if not isinstance(value, tuple):
                    value = (value,)
                invoke_in_main_thread(self._yield_slot, *value)
            yield value


# -----------------------------------------------------------------------------
# Main Thread Invocation
# -----------------------------------------------------------------------------
class _InvokeEvent(QEvent):
    """QEvent that carries a callable for main thread execution."""

    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__(_InvokeEvent.EVENT_TYPE)
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


_invoker = _Invoker()


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
        QCoreApplication.postEvent(_invoker, _InvokeEvent(fn, *args, **kwargs))


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
