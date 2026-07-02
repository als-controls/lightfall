from lightfall.monitor.models import Observation
from lightfall.monitor.rate_limiter import RateLimiter


def _obs(state_key, severity="warn"):
    return Observation(severity=severity, feed_name="f", run_uid="u",
                       title="t", message="m", state_key=state_key)


def test_surfaces_once_per_state_then_suppresses():
    rl = RateLimiter()
    assert rl.should_surface(_obs("low")) is True
    assert rl.should_surface(_obs("low")) is False           # same condition + severity
    assert rl.should_surface(_obs("low", "critical")) is True  # severity escalated
    assert rl.should_surface(_obs("other")) is True            # different condition


def test_reset_clears_state():
    rl = RateLimiter()
    rl.should_surface(_obs("low"))
    rl.reset()
    assert rl.should_surface(_obs("low")) is True
