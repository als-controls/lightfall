"""RunEndTrigger — fires on engine `stop` docs whose paired `start` matches a filter."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from loguru import logger

from lightfall.acquire.triggers.base import Trigger
from lightfall.acquire.triggers.filter import FilterPredicate


class RunEndTrigger(Trigger):
    """Fires on `stop` docs whose `run_start` was a 'start' matching `filter`.

    Maintains a small bounded LRU of recent start docs so a stop doc can be
    matched against its origin without round-tripping to Tiled.
    """

    _START_LRU_SIZE = 512

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
        self._starts: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def attach(self, manager) -> None:
        self._manager = manager
        self._token = manager.subscribe_engine(self._on_doc)

    def detach(self) -> None:
        if self._manager is not None and self._token is not None:
            self._manager.unsubscribe_engine(self._token)
        self._manager = None
        self._token = None
        self._starts.clear()

    def _remember_start(self, uid: str, doc: dict[str, Any]) -> None:
        self._starts[uid] = doc
        if len(self._starts) > self._START_LRU_SIZE:
            self._starts.popitem(last=False)

    def _on_doc(self, name: str, doc: dict[str, Any]) -> None:
        if name == "start":
            uid = doc.get("uid")
            if uid:
                self._remember_start(uid, doc)
            return
        if name != "stop":
            return
        start_uid = doc.get("run_start")
        if not start_uid:
            return
        start = self._starts.get(start_uid)
        if start is None:
            logger.debug("RunEndTrigger: stop for unknown start {}; ignoring", start_uid)
            return
        if not self._filter.matches(start):
            return
        self._manager.fire(
            pipeline=self._pipeline,
            run_uid=start_uid,
            parameters=dict(self._params),
            input_access_blob=start.get("access_blob") or {},
        )
