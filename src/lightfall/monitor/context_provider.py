"""Holds the current ExperimentContext and injects it into run start docs via
a BaseEngine pre-submit hook (same mechanism as the sample-metadata dialog)."""

from __future__ import annotations

import threading
from typing import Any

from lightfall.monitor.models import ExperimentContext


class ExperimentContextProvider:
    _instance: ExperimentContextProvider | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._ctx = ExperimentContext.default()

    @classmethod
    def get_instance(cls) -> ExperimentContextProvider:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def set_context(self, ctx: ExperimentContext) -> None:
        self._ctx = ctx

    def current(self) -> ExperimentContext:
        return self._ctx


def experiment_context_pre_submit(plan_name: str, kwargs: dict[str, Any]) -> dict | None:
    """Pre-submit hook: merge the current ExperimentContext into the start doc.

    Returns a dict merged into plan kwargs (and thus the start doc), or None to
    change nothing. Does not overwrite an explicit per-plan context."""
    if "experiment_context" in kwargs:
        return None
    ctx = ExperimentContextProvider.get_instance().current()
    return {"experiment_context": ctx.to_dict()}
