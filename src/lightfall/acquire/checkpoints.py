"""Position checkpoints: a log of where every positioner was, and a way back.

A checkpoint is a snapshot of the readback of every positioner in the device
catalog (top-level devices and their sub-devices, e.g. ``stage.x``). One is
taken automatically when an agent-submitted plan starts, and users or agents
can take one by hand. Checkpoints are appended to
``~/lightfall/checkpoints/checkpoints.jsonl``.

Restoring never moves anything directly: :func:`restore_plan` builds an
ordinary ``bps.mv`` plan that the caller submits to the RunEngine queue, so
a restore is queued, logged, pausable and visible like any other plan.

This partly restores the save/restore-positions feature of the BCS LabVIEW
tools.
"""

from __future__ import annotations

import json
import math
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from lightfall.utils.logging import logger

#: Positions closer than this (relative to max(1, |value|)) count as unchanged.
DEFAULT_TOLERANCE = 1e-4

_MAX_IN_MEMORY = 500


@dataclass(frozen=True)
class Checkpoint:
    id: str
    ts: float
    label: str
    source: str  # "agent" | "user" | "plan"
    positions: dict[str, float]
    reason: str = ""
    plan_name: str = ""
    skipped: tuple[str, ...] = field(default=())

    @property
    def display_time(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.ts))

    def to_json(self) -> str:
        d = asdict(self)
        d["skipped"] = list(self.skipped)
        return json.dumps(d)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        return cls(
            id=str(d["id"]),
            ts=float(d["ts"]),
            label=str(d.get("label", "")),
            source=str(d.get("source", "user")),
            positions={str(k): float(v) for k, v in (d.get("positions") or {}).items()},
            reason=str(d.get("reason", "")),
            plan_name=str(d.get("plan_name", "")),
            skipped=tuple(d.get("skipped") or ()),
        )


def _is_positioner(obj: Any) -> bool:
    return callable(getattr(obj, "set", None)) and hasattr(type(obj), "position")


def iter_positioners(devices: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    """Yield ``(dotted_name, positioner)`` for every positioner in ``devices``,
    including positioner sub-devices of composite devices (``stage.x``)."""
    for name, dev in devices.items():
        yield from _walk(name, dev, depth=0)


def _walk(name: str, obj: Any, depth: int) -> Iterator[tuple[str, Any]]:
    if _is_positioner(obj):
        yield name, obj
    if depth >= 4:
        return
    for attr in getattr(obj, "component_names", ()) or ():
        cpt = getattr(type(obj), attr, None)
        if getattr(cpt, "lazy", False):
            continue  # don't instantiate lazy components just to look at them
        try:
            child = getattr(obj, attr)
        except Exception as e:  # noqa: BLE001
            logger.debug("checkpoints: cannot read {}.{}: {}", name, attr, e)
            continue
        yield from _walk(f"{name}.{attr}", child, depth + 1)


def resolve(devices: dict[str, Any], dotted: str) -> Any | None:
    """Find ``stage.x`` in ``devices`` by attribute path."""
    head, _, rest = dotted.partition(".")
    obj = devices.get(head)
    for part in rest.split(".") if rest else ():
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def snapshot_positions(devices: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    """Current readback of every positioner; also the names that could not be read."""
    positions: dict[str, float] = {}
    skipped: list[str] = []
    for name, dev in iter_positioners(devices):
        try:
            value = dev.position
        except Exception:  # noqa: BLE001 - disconnected devices are skipped
            skipped.append(name)
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            positions[name] = float(value)
        else:
            skipped.append(name)
    return positions, skipped


def differs(a: float | None, b: float | None, tol: float = DEFAULT_TOLERANCE) -> bool:
    if a is None or b is None:
        return a is not b
    return abs(a - b) > tol * max(1.0, abs(b))


def restore_plan(devices: dict[str, Any], targets: dict[str, float]):
    """A plan moving each named positioner back to its checkpoint value.

    All moves are issued together and waited on as a group (``bps.mv``).
    Names are resolved now, not when the plan runs: raises KeyError for a
    name that no longer resolves to a positioner."""
    import bluesky.plan_stubs as bps

    args: list[Any] = []
    for name, value in targets.items():
        obj = resolve(devices, name)
        if obj is None or not _is_positioner(obj):
            raise KeyError(f"'{name}' is not a positioner in the device catalog")
        args.extend([obj, value])

    def _plan():
        if args:
            yield from bps.mv(*args)

    return _plan()


def _catalog_devices() -> dict[str, Any]:
    from lightfall.devices.catalog import DeviceCatalog

    return DeviceCatalog.get_instance().get_all_ophyd_devices()


class CheckpointStore(QObject):
    """Process-wide checkpoint log (JSONL on disk, recent ones in memory)."""

    added = Signal(object)  # Checkpoint

    _instance: CheckpointStore | None = None
    _lock = threading.Lock()

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self._path = path or (Path.home() / "lightfall" / "checkpoints" / "checkpoints.jsonl")
        self._items: list[Checkpoint] = []
        self._write_lock = threading.Lock()
        self._load()

    @classmethod
    def get_instance(cls) -> CheckpointStore:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            logger.warning("checkpoints: cannot read {}: {}", self._path, e)
            return
        for line in lines[-_MAX_IN_MEMORY:]:
            if not line.strip():
                continue
            try:
                self._items.append(Checkpoint.from_dict(json.loads(line)))
            except (ValueError, KeyError, TypeError) as e:
                logger.debug("checkpoints: bad line skipped: {}", e)

    def list(self) -> list[Checkpoint]:
        """Checkpoints, newest first."""
        return list(reversed(self._items))

    def get(self, checkpoint_id: str) -> Checkpoint | None:
        return next((c for c in self._items if c.id == checkpoint_id), None)

    def capture(
        self,
        label: str,
        *,
        source: str = "user",
        reason: str = "",
        plan_name: str = "",
        devices: dict[str, Any] | None = None,
    ) -> Checkpoint:
        """Snapshot all positioners now, append to the log, and emit ``added``."""
        positions, skipped = snapshot_positions(devices if devices is not None else _catalog_devices())
        return self._append(label, source, reason, plan_name, positions, skipped)

    def capture_if_changed(
        self,
        label: str,
        *,
        source: str = "user",
        reason: str = "",
        plan_name: str = "",
        devices: dict[str, Any] | None = None,
    ) -> Checkpoint | None:
        """Like :meth:`capture`, but skip it (return None) when every
        positioner is where the newest checkpoint already has it. This is
        what the RunEngine calls before each plan, so repeated counts or
        acquisitions at one position don't flood the log."""
        positions, skipped = snapshot_positions(devices if devices is not None else _catalog_devices())
        last = self._items[-1] if self._items else None
        if last is not None and set(last.positions) == set(positions) and not any(
            differs(positions[n], last.positions[n]) for n in positions
        ):
            return None
        return self._append(label, source, reason, plan_name, positions, skipped)

    def _append(
        self,
        label: str,
        source: str,
        reason: str,
        plan_name: str,
        positions: dict[str, float],
        skipped: list[str],
    ) -> Checkpoint:
        cp = Checkpoint(
            id=uuid.uuid4().hex[:12],
            ts=time.time(),
            label=label,
            source=source,
            positions=positions,
            reason=reason,
            plan_name=plan_name,
            skipped=tuple(skipped),
        )
        with self._write_lock:
            self._items.append(cp)
            del self._items[:-_MAX_IN_MEMORY]
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(cp.to_json() + "\n")
            except OSError as e:
                logger.warning("checkpoints: cannot write {}: {}", self._path, e)
        logger.info("Checkpoint {} '{}' ({} positioners)", cp.id, label, len(positions))
        self.added.emit(cp)
        return cp


def compare(cp: Checkpoint, devices: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Rows of ``{name, saved, current, changed}`` for a checkpoint vs. now."""
    current, _ = snapshot_positions(devices if devices is not None else _catalog_devices())
    rows = []
    for name, saved in sorted(cp.positions.items()):
        now = current.get(name)
        rows.append({"name": name, "saved": saved, "current": now, "changed": differs(now, saved)})
    return rows
