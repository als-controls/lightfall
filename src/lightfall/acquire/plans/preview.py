"""Dry-run preview of a Bluesky plan: what it would do, without doing it.

``preview_plan`` drives the plan generator through bluesky's
``RunEngineSimulator``, so no message reaches hardware: ``set`` / ``trigger``
/ ``kickoff`` are recorded, not executed. ``read`` and ``locate`` are answered
from the real devices (read-only) so plans that compute targets from
current positions (``rel_scan``, ``mvr``) preview with real numbers.

Caveat: the plan's own Python still runs. A plan that calls a device method
directly (``motor.set(1)`` instead of ``yield from bps.mv(motor, 1)``) would
act on hardware during a preview. That is a bug in such a plan, and the
plan-review skill tells the agent never to write one.

The summary is advisory: limit checks use each device's own ``check_value``
(or its ``limits``), and the duration is a rough lower bound from sleeps,
detector exposure times and move distance over motor velocity.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from lightfall.utils.logging import logger

#: Device names that deserve a second look before a plan touches them.
SENSITIVE_NAME_RE = re.compile(r"shutter|valve|gate|hv\b|high_?volt|bias|interlock", re.IGNORECASE)

#: A move covering more than this fraction of a motor's travel range is flagged.
LARGE_MOVE_FRACTION = 0.25

_MAX_MESSAGES = 200_000


@dataclass
class MoveSummary:
    device: str
    count: int = 0
    targets_min: float | None = None
    targets_max: float | None = None
    first_target: Any = None
    last_target: Any = None
    start_position: float | None = None
    limits: tuple[float, float] | None = None
    velocity: float | None = None
    travel: float = 0.0
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "moves": self.count,
            "target_range": [self.targets_min, self.targets_max],
            "first_target": _plain(self.first_target),
            "last_target": _plain(self.last_target),
            "start_position": self.start_position,
            "ends_at_start": _close(self.last_target, self.start_position),
            "limits": list(self.limits) if self.limits else None,
            "travel": round(self.travel, 6),
            "violations": self.violations[:5],
            "violation_count": len(self.violations),
        }


@dataclass
class PlanPreview:
    message_count: int = 0
    runs_opened: int = 0
    runs_closed: int = 0
    events: int = 0
    detectors: list[str] = field(default_factory=list)
    moves: dict[str, MoveSummary] = field(default_factory=dict)
    sleep_s: float = 0.0
    exposure_s: float = 0.0
    move_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    truncated: bool = False

    @property
    def estimated_duration_s(self) -> float:
        return self.sleep_s + self.exposure_s + self.move_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.error is None and not any(m.violations for m in self.moves.values()),
            "error": self.error,
            "messages": self.message_count,
            "truncated": self.truncated,
            "runs": {"opened": self.runs_opened, "closed": self.runs_closed},
            "events": self.events,
            "detectors": self.detectors,
            "moves": [m.to_dict() for m in self.moves.values()],
            "estimated_duration_s": round(self.estimated_duration_s, 1),
            "duration_breakdown_s": {
                "sleep": round(self.sleep_s, 1),
                "exposure": round(self.exposure_s, 1),
                "motion": round(self.move_s, 1),
            },
            "duration_note": (
                "Rough lower bound: sleeps + detector exposure times + travel/velocity. "
                "Readout, settling and per-point overhead are not included."
            ),
            "warnings": self.warnings,
        }


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool | None:
    if not _is_number(a) or not _is_number(b):
        return None
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def _fmt(v: Any) -> str:
    return f"{float(v):.6g}" if _is_number(v) else repr(v)


def _plain(v: Any) -> Any:
    """JSON-friendly scalar (numpy floats become Python floats)."""
    return float(v) if _is_number(v) else v


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - previews never fail on a bad read
        logger.trace("plan preview: device query failed: {}", e)
        return default


def _position(obj: Any) -> float | None:
    pos = _safe(lambda: obj.position)
    return float(pos) if _is_number(pos) else None


def _limits(obj: Any) -> tuple[float, float] | None:
    lim = _safe(lambda: obj.limits)
    if isinstance(lim, (tuple, list)) and len(lim) == 2 and all(_is_number(v) for v in lim):
        lo, hi = float(lim[0]), float(lim[1])
        if lo == hi:  # ophyd's (0, 0) means "no limits"
            return None
        return (min(lo, hi), max(lo, hi))
    return None


def _velocity(obj: Any) -> float | None:
    vel = _safe(lambda: obj.velocity.get())
    return float(vel) if _is_number(vel) and vel > 0 else None


def _exposure(obj: Any) -> float:
    for path in ("cam.acquire_time", "acquire_time", "exposure_time", "count_time", "preset_time"):
        target = obj
        for part in path.split("."):
            target = getattr(target, part, None)
            if target is None:
                break
        if target is None:
            continue
        value = _safe(lambda t=target: t.get())
        if _is_number(value) and value > 0:
            return float(value)
    return 0.0


def _check_value(obj: Any, value: Any) -> str | None:
    checker = getattr(obj, "check_value", None)
    if callable(checker):
        try:
            checker(value)
        except Exception as e:  # noqa: BLE001 - LimitError and friends
            return f"{_fmt(value)}: {e}"
        return None
    lim = _limits(obj)
    if lim and _is_number(value) and not (lim[0] <= float(value) <= lim[1]):
        return f"{_fmt(value)} outside limits {lim}"
    return None


def _read_response(obj: Any) -> Any:
    return _safe(lambda: obj.read(), default={}) or {}


def _locate_response(obj: Any) -> Any:
    located = _safe(lambda: obj.locate())
    if located is not None:
        return located
    pos = _position(obj)
    return {"setpoint": pos, "readback": pos}


def preview_plan(plan: Any, *, max_messages: int = _MAX_MESSAGES) -> PlanPreview:
    """Simulate ``plan`` (a generator) and summarise what it would do."""
    from bluesky.simulators import RunEngineSimulator

    preview = PlanPreview()
    seen: list = []
    sim = RunEngineSimulator()
    sim.add_handler("read", lambda msg: _read_response(msg.obj))
    sim.add_handler("locate", lambda msg: _locate_response(msg.obj))
    try:
        sim.simulate_plan(_bounded(plan, max_messages, preview, seen))
    except Exception as e:  # noqa: BLE001 - report, don't raise
        preview.error = f"{type(e).__name__}: {e}"
    _summarise(seen, preview)
    return preview


def _bounded(plan: Any, limit: int, preview: PlanPreview, seen: list):
    """Relay ``plan`` (sent values included), recording each message in
    ``seen`` and stopping after ``limit`` messages."""
    sent = None
    while True:
        try:
            msg = plan.send(sent)
        except StopIteration as stop:
            return stop.value
        if len(seen) >= limit:
            preview.truncated = True
            plan.close()
            return None
        seen.append(msg)
        sent = yield msg


def _summarise(messages: list, preview: PlanPreview) -> None:
    preview.message_count = len(messages)
    detectors: dict[str, None] = {}
    positions: dict[str, float | None] = {}
    for msg in messages:
        cmd = msg.command
        obj = msg.obj
        name = getattr(obj, "name", None) or repr(obj)
        if cmd == "open_run":
            preview.runs_opened += 1
        elif cmd == "close_run":
            preview.runs_closed += 1
        elif cmd == "save":
            preview.events += 1
        elif cmd == "sleep" and msg.args and _is_number(msg.args[0]):
            preview.sleep_s += float(msg.args[0])
        elif cmd == "trigger" and obj is not None:
            if name not in detectors:
                detectors[name] = None
            preview.exposure_s += _exposure(obj)
        elif cmd == "set" and obj is not None and msg.args:
            _record_move(preview, positions, name, obj, msg.args[0])
    preview.detectors = [d for d in detectors if d not in preview.moves]
    _add_warnings(preview)


def _record_move(preview: PlanPreview, positions: dict, name: str, obj: Any, target: Any) -> None:
    move = preview.moves.get(name)
    if move is None:
        move = MoveSummary(
            device=name,
            start_position=_position(obj),
            limits=_limits(obj),
            velocity=_velocity(obj),
        )
        move.first_target = target
        preview.moves[name] = move
        positions[name] = move.start_position
    move.count += 1
    move.last_target = target
    violation = _check_value(obj, target)
    if violation:
        move.violations.append(violation)
    if _is_number(target):
        t = float(target)
        move.targets_min = t if move.targets_min is None else min(move.targets_min, t)
        move.targets_max = t if move.targets_max is None else max(move.targets_max, t)
        prev = positions.get(name)
        if prev is not None:
            step = abs(t - prev)
            move.travel += step
            if move.velocity:
                preview.move_s += step / move.velocity
        positions[name] = t


def _add_warnings(preview: PlanPreview) -> None:
    w = preview.warnings
    if preview.error:
        w.append(f"The plan raised during simulation: {preview.error}")
    if preview.truncated:
        w.append("Simulation stopped at the message cap; the plan may be very long or unbounded.")
    if preview.runs_opened != preview.runs_closed:
        w.append(f"open_run/close_run mismatch ({preview.runs_opened} opened, {preview.runs_closed} closed).")
    if preview.runs_opened and not preview.detectors:
        w.append("The plan opens a run but never triggers a detector.")
    if preview.runs_opened and not preview.events:
        w.append("The plan opens a run but records no events.")
    for move in preview.moves.values():
        if move.violations:
            w.append(f"{move.device}: {len(move.violations)} target(s) rejected by limits, e.g. {move.violations[0]}")
        if SENSITIVE_NAME_RE.search(move.device):
            w.append(f"{move.device} looks like a shutter/valve/HV device; confirm with the user before running.")
        if move.limits and move.targets_min is not None and move.start_position is not None:
            span = move.limits[1] - move.limits[0]
            excursion = max(abs(move.targets_max - move.start_position),
                            abs(move.targets_min - move.start_position))
            if span > 0 and excursion / span > LARGE_MOVE_FRACTION:
                w.append(
                    f"{move.device}: moves up to {excursion:.4g} from its current position "
                    f"({excursion / span:.0%} of its travel range)."
                )
        ends = _close(move.last_target, move.start_position)
        if ends is False:
            w.append(
                f"{move.device}: left at {_fmt(move.last_target)} when the plan ends "
                f"(it starts at {_fmt(move.start_position)})."
            )
