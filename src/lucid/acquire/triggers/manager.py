"""TriggerManager — owns a set of configured Triggers, hooks BaseEngine.

The manager is engine-agnostic: it only uses BaseEngine.subscribe() /
unsubscribe() (`src/lucid/acquire/engine/base.py:396`). Triggers subscribe
through the manager so their tokens are tracked centrally.
"""
from __future__ import annotations

from typing import Any, Callable

from loguru import logger

from lucid.acquire.triggers.base import Trigger


class TriggerManager:
    """Owns Triggers, routes their `fire()` calls to a submit callable."""

    def __init__(
        self,
        engine: Any,
        submit_callable: Callable[..., None],
    ) -> None:
        self._engine = engine
        self._submit = submit_callable
        self._triggers: list[Trigger] = []
        self._engine_tokens: set[int] = set()

    def add(self, trigger: Trigger) -> None:
        self._triggers.append(trigger)
        trigger.attach(self)
        logger.debug("TriggerManager: added {}", type(trigger).__name__)

    def remove(self, trigger: Trigger) -> None:
        try:
            self._triggers.remove(trigger)
        except ValueError:
            return
        trigger.detach()
        logger.debug("TriggerManager: removed {}", type(trigger).__name__)

    def clear(self) -> None:
        for t in list(self._triggers):
            self.remove(t)

    def triggers(self) -> list[Trigger]:
        return list(self._triggers)

    def subscribe_engine(self, callback: Callable[[str, dict[str, Any]], Any]) -> int:
        tok = self._engine.subscribe(callback)
        self._engine_tokens.add(tok)
        return tok

    def unsubscribe_engine(self, token: int) -> None:
        self._engine.unsubscribe(token)
        self._engine_tokens.discard(token)

    def fire(self, *, pipeline: str, run_uid: str, parameters: dict[str, Any]) -> None:
        logger.info("TriggerManager: fire pipeline={} run_uid={}", pipeline, run_uid)
        self._submit(pipeline=pipeline, run_uid=run_uid, parameters=parameters)
