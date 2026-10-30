"""Agent usage telemetry: what agents cost and how they are used.

Every finished turn, tool call and permission decision is recorded:

- **Locally, always:** one JSON line per event in
  ``~/lightfall/telemetry/agent-usage.jsonl``, plus in-memory session totals
  (``AgentTelemetry.summary()``). Nothing leaves the machine.
- **To the facility LGTM stack, opt-in:** when the optional
  ``opentelemetry`` packages are installed (``pip install lightfall[telemetry]``)
  and an OTLP endpoint is configured, the same events become OTLP metrics
  (see :mod:`lightfall.agents.telemetry_otlp`).

Recorded per turn: agent name, backend (claude_code / agy), model, cost in
USD, input/output/cache tokens, wall and API duration, number of model
turns, and whether the turn errored. Per tool call: tool name and whether
its result was an error. Per permission prompt: tool name and the answer.
Prompts, tool arguments and replies are never recorded.
"""

from __future__ import annotations

import getpass
import json
import socket
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lightfall.utils.logging import logger

_NUMERIC_TURN_KEYS = (
    "total_cost_usd",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "duration_ms",
    "duration_api_ms",
    "num_turns",
)


@dataclass
class _Totals:
    turns: int = 0
    errors: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    approvals: int = 0
    denials: int = 0
    sums: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def to_dict(self) -> dict[str, Any]:
        d = {
            "turns": self.turns,
            "turn_errors": self.errors,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "permission_approved": self.approvals,
            "permission_denied": self.denials,
        }
        d.update({k: round(v, 6) for k, v in self.sums.items()})
        return d


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _beamline() -> str:
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        return str(PreferencesManager.get_instance().get("tiled_beamline", "") or "")
    except Exception:  # noqa: BLE001
        return ""


class AgentTelemetry:
    """Process-wide recorder. Thread-safe; cheap enough to call per event."""

    _instance: AgentTelemetry | None = None
    _lock = threading.Lock()

    def __init__(self, path: Path | None = None, exporter: Any | None = None) -> None:
        self._path = path or (Path.home() / "lightfall" / "telemetry" / "agent-usage.jsonl")
        self._write_lock = threading.Lock()
        self._started = time.time()
        self._totals: dict[str, _Totals] = defaultdict(_Totals)
        self._exporter = exporter
        self._host = socket.gethostname()
        try:
            self._user = getpass.getuser()
        except Exception:  # noqa: BLE001
            self._user = ""

    @classmethod
    def get_instance(cls) -> AgentTelemetry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = cls()
                    inst._exporter = _make_exporter()
                    cls._instance = inst
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            if cls._instance is not None and cls._instance._exporter is not None:
                try:
                    cls._instance._exporter.shutdown()
                except Exception:  # noqa: BLE001
                    pass
            cls._instance = None

    @property
    def path(self) -> Path:
        return self._path

    # --- recording ------------------------------------------------------

    def record_turn(self, agent: str, backend: str, model: str | None, info: dict[str, Any]) -> None:
        values = {k: _number(info.get(k)) for k in _NUMERIC_TURN_KEYS if k in info}
        is_error = bool(info.get("is_error"))
        t = self._totals[agent]
        t.turns += 1
        t.errors += int(is_error)
        for k, v in values.items():
            t.sums[k] += v
        self._write({
            "event": "turn", "agent": agent, "backend": backend,
            "model": model or info.get("model") or "", "is_error": is_error, **values,
        })
        self._export("turn", agent=agent, backend=backend, model=model or "", values=values,
                     is_error=is_error)

    def record_tool(self, agent: str, tool: str, is_error: bool) -> None:
        t = self._totals[agent]
        t.tool_calls += 1
        t.tool_errors += int(is_error)
        self._write({"event": "tool", "agent": agent, "tool": tool, "is_error": is_error})
        self._export("tool", agent=agent, tool=tool, is_error=is_error)

    def record_permission(self, agent: str, tool: str, allowed: bool, reason: str = "") -> None:
        t = self._totals[agent]
        if allowed:
            t.approvals += 1
        else:
            t.denials += 1
        self._write({"event": "permission", "agent": agent, "tool": tool,
                     "allowed": allowed, "outcome": reason or ("allowed" if allowed else "denied")})
        self._export("permission", agent=agent, tool=tool, allowed=allowed)

    # --- reading --------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Totals since this Lightfall session started, per agent and overall."""
        overall = _Totals()
        for t in self._totals.values():
            overall.turns += t.turns
            overall.errors += t.errors
            overall.tool_calls += t.tool_calls
            overall.tool_errors += t.tool_errors
            overall.approvals += t.approvals
            overall.denials += t.denials
            for k, v in t.sums.items():
                overall.sums[k] += v
        return {
            "since": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._started)),
            "overall": overall.to_dict(),
            "agents": {name: t.to_dict() for name, t in sorted(self._totals.items())},
            "log": str(self._path),
            "otlp_export": self._exporter is not None,
        }

    # --- internals ------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        record = {"ts": time.time(), "host": self._host, "user": self._user,
                  "beamline": _beamline(), **record}
        line = json.dumps(record, default=str)
        with self._write_lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError as e:
                logger.debug("agent telemetry: cannot write {}: {}", self._path, e)

    def _export(self, kind: str, **fields: Any) -> None:
        if self._exporter is None:
            return
        try:
            self._exporter.record(kind, beamline=_beamline(), **fields)
        except Exception as e:  # noqa: BLE001 - telemetry must never break an agent
            logger.debug("agent telemetry export failed: {}", e)


def _make_exporter() -> Any | None:
    try:
        from lightfall.agents.telemetry_otlp import make_exporter

        return make_exporter()
    except Exception as e:  # noqa: BLE001
        logger.debug("agent telemetry: OTLP export unavailable: {}", e)
        return None


def attach_worker(worker: Any, *, agent: str, backend: str, model_fn) -> None:
    """Record ``worker``'s turns and tool calls (AgentWorker or the legacy worker).

    ``model_fn`` returns the session's current model, so a model switched
    mid-session is attributed correctly."""
    from collections import deque

    # Tool calls carry no id on the call side, and results arrive in call
    # order, so match them first-in first-out.
    pending: deque[str] = deque(maxlen=256)

    def on_result(info: dict) -> None:
        pending.clear()
        AgentTelemetry.get_instance().record_turn(agent, backend, model_fn(), dict(info or {}))

    def on_tool_called(name: str, _tool_input: dict) -> None:
        pending.append(name)

    def on_tool_result(_tool_use_id: str, payload: dict) -> None:
        name = pending.popleft() if pending else "unknown"
        is_error = bool((payload or {}).get("is_error")) if isinstance(payload, dict) else False
        AgentTelemetry.get_instance().record_tool(agent, name, is_error)

    worker.result_received.connect(on_result)
    worker.tool_called.connect(on_tool_called)
    worker.tool_result.connect(on_tool_result)
    # A cancelled or failed turn can leave a call without its result; drop
    # the leftovers or every tool in the next turn is misattributed by one.
    for name in ("query_cancelled", "error_occurred"):
        signal = getattr(worker, name, None)
        if signal is not None:
            signal.connect(lambda *_args: pending.clear())


def summarize_log(path: Path, since_days: float | None = None) -> list[dict[str, Any]]:
    """Aggregate an agent-usage log by (user, beamline, agent, model) for turns."""
    cutoff = time.time() - since_days * 86400 if since_days else None
    groups: dict[tuple, dict[str, Any]] = {}
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") != "turn" or (cutoff and rec.get("ts", 0) < cutoff):
            continue
        key = (rec.get("user", ""), rec.get("beamline", ""), rec.get("agent", ""), rec.get("model", ""))
        g = groups.setdefault(key, {
            "user": key[0], "beamline": key[1], "agent": key[2], "model": key[3],
            "turns": 0, "errors": 0, **dict.fromkeys(_NUMERIC_TURN_KEYS, 0.0),
        })
        g["turns"] += 1
        g["errors"] += int(bool(rec.get("is_error")))
        for k in _NUMERIC_TURN_KEYS:
            g[k] += _number(rec.get(k))
    return sorted(groups.values(), key=lambda g: -g["total_cost_usd"])


def main(argv: list[str] | None = None) -> int:
    """``python -m lightfall.agents.telemetry [--days N] [--json] [log]``."""
    import argparse

    parser = argparse.ArgumentParser(description="Summarise Lightfall agent usage.")
    parser.add_argument("log", nargs="?", type=Path,
                        default=Path.home() / "lightfall" / "telemetry" / "agent-usage.jsonl")
    parser.add_argument("--days", type=float, default=None, help="Only the last N days.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    args = parser.parse_args(argv)
    rows = summarize_log(args.log, args.days)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    header = f"{'user':<12} {'beamline':<10} {'agent':<16} {'model':<12} {'turns':>6} {'cost $':>9} {'in tok':>10} {'out tok':>9}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['user']:<12} {r['beamline']:<10} {r['agent']:<16} {r['model']:<12} "
              f"{r['turns']:>6} {r['total_cost_usd']:>9.2f} {int(r['input_tokens']):>10} "
              f"{int(r['output_tokens']):>9}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
