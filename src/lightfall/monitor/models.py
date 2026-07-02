"""Pure data types for the proactive monitor.

Observation is what a feed emits. ExperimentContext is the launch-time
"what is this measurement trying to do" object front-loaded into the run
start document. Both are JSON-serializable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["info", "warn", "critical"]


@dataclass
class Observation:
    """A single judgment emitted by a MonitorFeed."""

    severity: Severity
    feed_name: str
    run_uid: str
    title: str
    message: str
    state_key: str  # identity of the *condition*, for rate-limiting
    metrics: dict[str, float] = field(default_factory=dict)
    recommendation: str | None = None
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "feed_name": self.feed_name,
            "run_uid": self.run_uid,
            "title": self.title,
            "message": self.message,
            "state_key": self.state_key,
            "metrics": dict(self.metrics),
            "recommendation": self.recommendation,
            "ts": self.ts,
        }


@dataclass
class ExperimentContext:
    """Declared intent for a measurement, read by feeds (and, in Plan B,
    the advisor). Front-loaded into the start doc under key
    ``experiment_context``."""

    experiment_type: str = "generic"
    intent: str = ""
    feed_config: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def default(cls) -> ExperimentContext:
        return cls()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExperimentContext:
        return cls(
            experiment_type=str(d.get("experiment_type", "generic")),
            intent=str(d.get("intent", "")),
            feed_config=dict(d.get("feed_config", {}) or {}),
        )

    @classmethod
    def from_start_doc(cls, doc: dict[str, Any]) -> ExperimentContext:
        blob = doc.get("experiment_context")
        if isinstance(blob, dict):
            return cls.from_dict(blob)
        return cls.default()

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_type": self.experiment_type,
            "intent": self.intent,
            "feed_config": dict(self.feed_config),
        }

    def for_feed(self, name: str) -> dict[str, Any]:
        cfg = self.feed_config.get(name)
        return dict(cfg) if isinstance(cfg, dict) else {}
