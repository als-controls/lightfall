from __future__ import annotations

from unittest.mock import MagicMock

from lightfall.acquire.engine.bluesky import BlueskyEngine


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
