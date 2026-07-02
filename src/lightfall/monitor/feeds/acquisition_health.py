"""Beamline-agnostic acquisition-health feed.

Judges IOC-provided inline scalars only (no asset reads, no reduction):
detects a stalled run and count-rate collapse. Config via
ctx.for_feed("acquisition_health"): count_field, min_rate, min_samples,
stall_after_s."""

from __future__ import annotations

from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.models import ExperimentContext, Observation
from lightfall.monitor.monitor_plugin import MonitorPlugin

FEED_NAME = "acquisition_health"


class AcquisitionHealthFeed(MonitorFeed):
    name = FEED_NAME
    default_interval_s = 30.0

    def evaluate(
        self, ctx: ExperimentContext, window: DataWindow, prior: list[Observation]
    ) -> Observation | None:
        cfg = ctx.for_feed(FEED_NAME)
        stall_after = float(cfg.get("stall_after_s", 60.0))
        # Stall: events have stopped arriving while the run is active.
        if window.event_count > 0 and window.age_s is not None and window.age_s > stall_after:
            return Observation(
                severity="warn", feed_name=FEED_NAME, run_uid=window.run_uid,
                title="Acquisition stalled",
                message=f"No new events for {window.age_s:.0f}s (> {stall_after:.0f}s).",
                state_key=f"{FEED_NAME}:stalled",
                metrics={"age_s": float(window.age_s)},
                recommendation="Check the detector / shutter / plan progress.",
            )
        # Count-rate collapse.
        count_field = cfg.get("count_field")
        min_rate = float(cfg.get("min_rate", 0.0))
        min_samples = int(cfg.get("min_samples", 3))
        if count_field:
            series = [float(v) for v in window.series(count_field) if isinstance(v, (int, float))]
            if len(series) >= min_samples:
                recent = series[-min_samples:]
                mean = sum(recent) / len(recent)
                if mean < min_rate:
                    return Observation(
                        severity="warn", feed_name=FEED_NAME, run_uid=window.run_uid,
                        title="Count rate collapsed",
                        message=f"Mean of last {min_samples} '{count_field}' = "
                                f"{mean:.3g} < {min_rate:.3g}.",
                        state_key=f"{FEED_NAME}:low_rate",
                        metrics={"mean_rate": mean, "min_rate": min_rate},
                        recommendation="Check beam / shutter / sample alignment.",
                    )
        return None


class AcquisitionHealthMonitorPlugin(MonitorPlugin):
    @property
    def name(self) -> str:
        return FEED_NAME

    @property
    def description(self) -> str:
        return "Warns on stalled acquisition or count-rate collapse."

    @property
    def category(self) -> str:
        return "acquisition"

    def create_feeds(self) -> list[MonitorFeed]:
        return [AcquisitionHealthFeed()]
