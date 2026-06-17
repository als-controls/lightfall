from __future__ import annotations

import time
from unittest.mock import MagicMock

from bluesky import RunEngine

from lightfall.acquire.engine.bluesky import BlueskyEngine


def _wait_until(cond, timeout: float = 5.0, interval: float = 0.02) -> bool:
    """Poll ``cond`` until true or timeout; returns whether it became true."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(interval)
    return False


def _adopted_mock_re() -> MagicMock:
    re = MagicMock(name="AdoptedRE")
    re.state = "idle"
    re.subscribe.return_value = 1
    return re


def test_adopt_uses_external_run_engine():
    engine = BlueskyEngine(toast_notifications=False)

    fake_re = MagicMock(name="RunEngine")
    fake_re.state = "idle"
    # subscribe must return an int token like the real RE
    fake_re.subscribe.return_value = 1

    engine.adopt(fake_re)

    # The adopted RE is exactly the object we passed in.
    assert engine.RE is fake_re
    # Adoption wired the document subscription and the waiting hook.
    fake_re.subscribe.assert_called_once()
    assert fake_re.waiting_hook is engine.waiting_bridge
    # The engine is marked adopted so the queue processor won't rebuild it.
    assert engine._adopted is True


def test_adopt_disposes_orphan_eager_run_engine():
    """If the queue processor already eagerly built its own RE, adopt() must
    dispose that orphan's event loop so its background thread does not leak."""
    engine = BlueskyEngine(toast_notifications=False)

    # Wait for the queue processor to eagerly create its own RunEngine.
    assert _wait_until(lambda: isinstance(engine._RE, RunEngine)), (
        "queue processor never created its eager RunEngine"
    )
    orphan_loop = engine._loop
    assert orphan_loop is not None and orphan_loop.is_running()

    engine.adopt(_adopted_mock_re())

    assert engine.RE is not None and not isinstance(engine.RE, RunEngine)
    assert engine._adopted is True
    # The orphan's loop is stopped (its bluesky-run-engine thread then exits).
    assert _wait_until(lambda: not orphan_loop.is_running()), (
        "orphan RunEngine loop was not disposed by adopt()"
    )


def test_adopt_is_not_overwritten_by_queue_processor():
    """After adopt(), the adopted RE must remain even once the worker thread
    has had time to run its creation block (no orphan overwrite)."""
    engine = BlueskyEngine(toast_notifications=False)
    fake_re = _adopted_mock_re()
    engine.adopt(fake_re)

    # Give the worker thread ample time to reach its creation block.
    time.sleep(0.3)
    assert engine.RE is fake_re
    assert engine._adopted is True
