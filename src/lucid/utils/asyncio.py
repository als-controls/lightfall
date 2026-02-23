"""Asyncio utilities for LUCID.

Provides helpers for scheduling coroutines on the Qt-integrated asyncio
event loop (via PySide6.QtAsyncio). The event loop is started by
``NCSApplication.run()`` and is available for the entire application lifetime.

Usage from any thread::

    from lucid.utils.asyncio import schedule_coroutine

    # Fire-and-forget
    schedule_coroutine(some_async_function())

    # With callback
    schedule_coroutine(fetch_data(), callback=on_data_ready)
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from lucid.utils.logging import logger

__all__ = [
    "get_event_loop",
    "schedule_coroutine",
    "run_coroutine_sync",
]


def get_event_loop() -> asyncio.AbstractEventLoop:
    """Get the running Qt-integrated asyncio event loop.

    Returns:
        The running event loop.

    Raises:
        RuntimeError: If no event loop is running (application not started).
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        # Not in an async context — get the loop from the policy
        return asyncio.get_event_loop()


def schedule_coroutine(
    coro: Coroutine[Any, Any, Any],
    *,
    callback: Callable[[Any], None] | None = None,
    error_callback: Callable[[Exception], None] | None = None,
) -> asyncio.Task:
    """Schedule a coroutine on the Qt-integrated event loop.

    Safe to call from any thread. The coroutine runs on the main thread's
    event loop (integrated with Qt).

    Args:
        coro: The coroutine to schedule.
        callback: Called with the result on success.
        error_callback: Called with the exception on failure.

    Returns:
        The asyncio Task.
    """
    loop = get_event_loop()

    def _create_task() -> asyncio.Task:
        task = loop.create_task(coro)
        if callback or error_callback:
            task.add_done_callback(lambda t: _handle_done(t, callback, error_callback))
        return task

    if loop.is_running():
        # Schedule from another thread (or same thread)
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        # Wrap in done callback
        if callback or error_callback:
            future.add_done_callback(
                lambda f: _handle_done_future(f, callback, error_callback)
            )
        # Return a pseudo-task (concurrent.futures.Future)
        return future  # type: ignore[return-value]
    else:
        return _create_task()


def _handle_done(
    task: asyncio.Task,
    callback: Callable[[Any], None] | None,
    error_callback: Callable[[Exception], None] | None,
) -> None:
    """Handle task completion."""
    exc = task.exception()
    if exc is not None:
        if error_callback:
            error_callback(exc)
        else:
            logger.error("Unhandled error in scheduled coroutine: {}", exc)
    elif callback:
        callback(task.result())


def _handle_done_future(
    future: Any,
    callback: Callable[[Any], None] | None,
    error_callback: Callable[[Exception], None] | None,
) -> None:
    """Handle concurrent.futures.Future completion."""
    try:
        exc = future.exception()
        if exc is not None:
            if error_callback:
                error_callback(exc)
            else:
                logger.error("Unhandled error in scheduled coroutine: {}", exc)
        elif callback:
            callback(future.result())
    except Exception as e:
        logger.error("Error in done callback: {}", e)


def run_coroutine_sync(coro: Coroutine[Any, Any, Any], timeout: float = 30.0) -> Any:
    """Run a coroutine synchronously, blocking until complete.

    If an event loop is already running (e.g. the Qt loop), schedules
    the coroutine on it and blocks via concurrent.futures. Otherwise
    falls back to asyncio.run().

    Args:
        coro: The coroutine to run.
        timeout: Maximum seconds to wait.

    Returns:
        The coroutine's return value.

    Raises:
        TimeoutError: If the coroutine doesn't complete in time.
        RuntimeError: If the coroutine raises.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    else:
        return asyncio.run(coro)
