# src/lightfall/monitor/feed.py
"""MonitorFeed: the pluggable unit of judgment.

A feed is deterministic and cheap — it JUDGES reduced signals against
experiment intent. It must not perform data reduction (see spec §Goal).
``prior`` is the run's already-surfaced observations, so a feed can express
"low and not improving" without ad-hoc state."""

from __future__ import annotations

from abc import ABC, abstractmethod

from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.models import ExperimentContext, Observation


class MonitorFeed(ABC):
    name: str = "feed"
    default_interval_s: float = 30.0

    @abstractmethod
    def evaluate(
        self,
        ctx: ExperimentContext,
        window: DataWindow,
        prior: list[Observation],
    ) -> Observation | None:
        """Return an Observation to surface, or None if nothing to report."""
        ...
