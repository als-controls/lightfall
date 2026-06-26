"""Connect ophyd-async devices on the Bluesky engine's running event loop.

Lightfall's device pipeline (DeviceConnectionManager -> backend.instantiate ->
backend.check_connection) was built for classic (threaded) ophyd, which
connects synchronously via wait_for_connection()/`.connected`. ophyd-async
devices instead require ``await device.connect(...)``. This module drives that
connect on the BlueskyEngine's asyncio loop -- the same loop the RunEngine
later uses to operate the device, so loop affinity is preserved.

Async devices are detected structurally so lightfall core never imports
ophyd_async.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from lightfall.acquire.engine import get_engine
from lightfall.utils.logging import logger


def is_async_connectable(obj: Any) -> bool:
    """True if *obj* exposes an awaitable ``connect`` (ophyd-async style)."""
    return inspect.iscoroutinefunction(getattr(obj, "connect", None))


def _get_engine_loop(loop_wait: float) -> "asyncio.AbstractEventLoop | None":
    """Return the engine's running event loop, waiting up to *loop_wait* seconds."""
    deadline = time.monotonic() + loop_wait
    while True:
        try:
            engine = get_engine()
        except Exception as exc:  # engine not configured yet
            logger.warning("async connect: get_engine() failed: {}", exc)
            engine = None
        loop = getattr(engine, "event_loop", None) if engine is not None else None
        if loop is not None and loop.is_running():
            return loop
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def connect_async_device(
    obj: Any,
    *,
    mock: bool = False,
    timeout: float = 5.0,
    loop_wait: float = 5.0,
) -> bool:
    """Drive ``await obj.connect(mock=mock)`` on the engine loop; return success.

    Returns ``False`` (logging a reason) if no running engine loop becomes
    available within *loop_wait*, or if ``connect()`` raises or exceeds
    *timeout*. Never hangs.
    """
    loop = _get_engine_loop(loop_wait)
    if loop is None:
        logger.error(
            "async connect: no running engine event loop for '{}'; "
            "device left unconnected",
            getattr(obj, "name", obj),
        )
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(obj.connect(mock=mock), loop)
        future.result(timeout=timeout)
        return True
    except Exception as exc:
        logger.warning(
            "async connect: connect() failed for '{}': {}",
            getattr(obj, "name", obj),
            exc,
        )
        return False
