"""BlueskyEngine.event_loop exposes the engine's asyncio loop."""
import asyncio

from lightfall.acquire.engine.bluesky import BlueskyEngine


def test_event_loop_is_none_before_start():
    engine = BlueskyEngine()
    assert engine.event_loop is None


def test_event_loop_returns_underlying_loop():
    engine = BlueskyEngine()
    loop = asyncio.new_event_loop()
    try:
        engine._loop = loop  # simulate the loop the RunEngine created
        assert engine.event_loop is loop
    finally:
        loop.close()
