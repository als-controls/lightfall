"""Surface an observation only when its (state_key, severity) changes, so a
standing condition is announced once, not every tick."""

from __future__ import annotations

from lightfall.monitor.models import Observation


class RateLimiter:
    def __init__(self) -> None:
        self._state: dict[str, str] = {}

    def should_surface(self, obs: Observation) -> bool:
        if self._state.get(obs.state_key) == obs.severity:
            return False
        self._state[obs.state_key] = obs.severity
        return True

    def reset(self) -> None:
        self._state.clear()
