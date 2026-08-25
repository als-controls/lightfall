"""Compatibility shim: threading utilities now live in lightfall-utils."""

from lightfall_utils.threads import (  # noqa: F401
    ManagedThreadPool,
    QThreadFuture,
    QThreadFutureIterator,
    ThreadManager,
    get_thread_manager,
    initialize_main_thread_invoker,
    invoke_as_event,
    invoke_in_main_thread,
    is_main_thread,
    iterator,
    method,
    thread_manager,
)

__all__ = [
    "ThreadManager",
    "ManagedThreadPool",
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
