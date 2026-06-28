"""Reduced-signal view passed to a MonitorFeed each tick.

Holds ONLY already-reduced signals: inline scalar event columns from the
rolling buffer, plus optional hooks for externally-computed metrics
(`derived`) and a rare direct PV read (`pv_get`). There is deliberately
no raw-frame facet — feeds never analyse raw data (see spec §Goal)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataWindow:
    run_uid: str
    events: dict[str, list[Any]] = field(default_factory=dict)
    seq_nums: list[int] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    event_count: int = 0
    age_s: float | None = None  # seconds since the last event (None if no events)
    derived_provider: Callable[[str], dict | None] | None = None
    pv_getter: Callable[[str], Any] | None = None

    def latest(self, field_name: str) -> Any | None:
        seq = self.events.get(field_name)
        return seq[-1] if seq else None

    def series(self, field_name: str, last_k: int | None = None) -> list[Any]:
        seq = list(self.events.get(field_name, []))
        return seq[-last_k:] if last_k else seq

    def derived(self, name: str) -> dict | None:
        if self.derived_provider is not None:
            return self.derived_provider(name)
        return None

    def pv_get(self, pv: str) -> Any:
        if self.pv_getter is not None:
            return self.pv_getter(pv)
        raise NotImplementedError("pv_get is not wired in this DataWindow")
