"""RunStartTrigger — fires on engine `start` docs matching a filter."""
from __future__ import annotations

from typing import Any

from loguru import logger

from lucid.acquire.triggers.base import Trigger
from lucid.acquire.triggers.filter import FilterPredicate


class RunStartTrigger(Trigger):
    """Fires `manager.fire()` for each engine 'start' doc matching `filter`."""

    def __init__(
        self,
        *,
        filter: FilterPredicate,
        pipeline: str,
        parameter_overrides: dict[str, Any],
    ) -> None:
        self._filter = filter
        self._pipeline = pipeline
        self._params = dict(parameter_overrides)
        self._manager = None
        self._token: int | None = None

    def attach(self, manager) -> None:
        self._manager = manager
        self._token = manager.subscribe_engine(self._on_doc)

    def detach(self) -> None:
        if self._manager is not None and self._token is not None:
            self._manager.unsubscribe_engine(self._token)
        self._manager = None
        self._token = None

    def _on_doc(self, name: str, doc: dict[str, Any]) -> None:
        if name != "start":
            return
        if not self._filter.matches(doc):
            return
        uid = doc.get("uid")
        if not uid:
            logger.warning("RunStartTrigger: matching start doc has no uid; skipping")
            return
        self._manager.fire(
            pipeline=self._pipeline,
            run_uid=uid,
            parameters=dict(self._params),
            input_access_blob=doc.get("access_blob") or {},
        )
