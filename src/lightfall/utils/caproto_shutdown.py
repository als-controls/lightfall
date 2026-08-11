"""Stop caproto's user-callback thread pools cleanly at application shutdown.

Each connected caproto virtual circuit owns a *non-daemon*
``ThreadPoolExecutor`` (``user_callback_executor``) that runs user
subscription/read/write callbacks. ``concurrent.futures`` registers an
``atexit`` hook that joins every executor's worker threads at interpreter
shutdown, so a callback still in flight there stalls exit -- the reason
``main.py`` installs a force-exit watchdog. Draining these executors (without
tearing down sockets) lets the process exit on its own.

``get_caproto_context`` never *creates* a context: at shutdown we only want
the one caproto already built, not a fresh one (which would spawn threads).
"""
from __future__ import annotations

from typing import Any

from lightfall.utils.logging import logger

__all__ = ["get_caproto_context", "drain_callback_executors", "disconnect_context"]


def get_caproto_context() -> Any | None:
    """Return caproto's already-created shared Context, or ``None``.

    Returns ``None`` when caproto isn't installed, its threading control layer
    was never used, or anything goes wrong. Never creates a new context (that
    would spin up broadcaster/selector threads during shutdown).
    """
    try:
        from caproto.threading.pyepics_compat import _make_context

        # _make_context is functools.lru_cache(1). Only return a context if one
        # was actually built (currsize > 0) so we never construct one here.
        if _make_context.cache_info().currsize == 0:
            return None
        return _make_context()
    except Exception:
        return None


def drain_callback_executors(ctx: Any | None) -> int:
    """Shut down every circuit's user-callback ``ThreadPoolExecutor``.

    Uses ``wait=False, cancel_futures=True``: queued callbacks are dropped and
    no new work is accepted, so the non-daemon worker threads finish and stop
    blocking interpreter exit. Does **not** touch sockets, so it cannot trigger
    the socket-teardown crash that a full ``Context.disconnect()`` is suspected
    of. Returns the number of executors drained. Never raises.
    """
    if ctx is None:
        return 0
    drained = 0
    try:
        circuit_managers = getattr(ctx, "circuit_managers", None) or {}
        for cm in list(circuit_managers.values()):
            executor = getattr(cm, "user_callback_executor", None)
            if executor is None:
                continue
            try:
                executor.shutdown(wait=False, cancel_futures=True)
                drained += 1
            except Exception as e:
                logger.warning(
                    "Failed to shut down a caproto callback executor: {}", e
                )
    except Exception as e:
        logger.warning("Draining caproto callback executors failed: {}", e)
    return drained


def disconnect_context(ctx: Any | None) -> bool:
    """Fully disconnect a caproto ``Context`` (circuits + sockets + selector).

    This is the thorough teardown -- besides draining the callback executors it
    also stops caproto's selector/circuit threads before interpreter
    finalization, which the drain path deliberately leaves running. The socket
    teardown here is what has historically been suspected of a Windows access
    violation, so callers gate this behind an opt-in. Returns ``True`` if
    ``disconnect()`` was invoked. Never raises at the Python level (a hard
    native crash, if it really happens, cannot be caught here).
    """
    if ctx is None:
        return False
    try:
        ctx.disconnect(wait=False)
        return True
    except Exception as e:
        logger.warning("caproto Context.disconnect() failed: {}", e)
        return False
