"""ALS beam-status feed: flags beam loss and low ring current during a run.

Reads the ALSBeamStatusService that the status-bar button already polls.
It never creates, starts, or polls that service itself, so an offline
beamline (no network, no proxy, status-bar button disabled) sees no
requests and no observations from this feed.

Only *changes during the run* are reported: a run that starts with the
beam already down (offline work, sim devices, machine shutdown) stays
silent. Config via ctx.for_feed("beam_status"): min_current_ma (off by
default), max_age_s (default 300; older beam data counts as unknown)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.models import ExperimentContext, Observation
from lightfall.monitor.monitor_plugin import MonitorPlugin

FEED_NAME = "beam_status"


def _running_service_data() -> Any | None:
    """Current ALSBeamData from an already-running, connected service, else None."""
    from lightfall.services.als_beam_status import ALSBeamStatusService

    service = ALSBeamStatusService._instance
    if service is None or not service.is_polling or not service.is_connected:
        return None
    return service.current_data


class BeamStatusFeed(MonitorFeed):
    name = FEED_NAME
    default_interval_s = 30.0

    def __init__(self, source: Callable[[], Any | None] = _running_service_data) -> None:
        self._source = source
        self._run_uid: str | None = None
        self._seen_available = False
        self._lost = False

    def evaluate(
        self, ctx: ExperimentContext, window: DataWindow, prior: list[Observation]
    ) -> Observation | None:
        if window.run_uid != self._run_uid:
            self._run_uid = window.run_uid
            self._seen_available = False
            self._lost = False

        data = self._source()
        if data is None or not self._fresh(data, ctx):
            return None

        if data.beam_available:
            if self._lost:
                self._lost = False
                return self._obs(
                    "info", window, "beam_restored", "Beam restored",
                    f"Beam is back at {data.beam_current:.1f} mA.", data,
                    "Points measured while the beam was down may need re-measuring.",
                )
            self._seen_available = True
            return self._low_current(ctx, window, data)

        if self._seen_available and not self._lost:
            self._lost = True
            comment = f" Operations: {data.comment}" if data.comment else ""
            return self._obs(
                "critical", window, "beam_lost", "Beam lost",
                f"Beam became unavailable during the run.{comment}", data,
                "Pause the queue until the beam returns.",
            )
        return None

    def _low_current(
        self, ctx: ExperimentContext, window: DataWindow, data: Any
    ) -> Observation | None:
        floor = ctx.for_feed(FEED_NAME).get("min_current_ma")
        if floor is None or data.beam_current >= float(floor):
            return None
        return self._obs(
            "warn", window, "low_current", "Ring current low",
            f"Ring current {data.beam_current:.1f} mA is below {float(floor):.1f} mA.",
            data, "Normalise by I0, or wait for the next fill.",
        )

    @staticmethod
    def _fresh(data: Any, ctx: ExperimentContext) -> bool:
        stamp = getattr(data, "timestamp", None)
        if stamp is None:
            return True
        max_age = float(ctx.for_feed(FEED_NAME).get("max_age_s", 300.0))
        return time.time() - stamp.timestamp() <= max_age

    @staticmethod
    def _obs(severity, window, key, title, message, data, recommendation) -> Observation:
        return Observation(
            severity=severity, feed_name=FEED_NAME, run_uid=window.run_uid,
            title=title, message=message, state_key=f"{FEED_NAME}:{key}",
            metrics={"beam_current_ma": float(data.beam_current),
                     "beam_available": float(bool(data.beam_available))},
            recommendation=recommendation,
        )


class BeamStatusMonitorPlugin(MonitorPlugin):
    @property
    def name(self) -> str:
        return FEED_NAME

    @property
    def description(self) -> str:
        return "Flags ALS beam loss and low ring current during a run (silent when offline)."

    @property
    def category(self) -> str:
        return "facility"

    def create_feeds(self) -> list[MonitorFeed]:
        return [BeamStatusFeed()]
