"""Run scenarios x agents x seeds; one fresh sim environment per run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from lightfall.eval.scenarios import SCENARIOS


def run_one(scenario_name: str, agent, seed: str | None, workdir: Path | None = None) -> dict[str, Any]:
    """One evaluation in this process (Lightfall's singletons make one
    environment per process the safe unit; see :func:`run_many`)."""
    from lightfall.eval.environment import SimEnvironment

    scenario = SCENARIOS[scenario_name]()
    with SimEnvironment(seed=seed, workdir=workdir, role=scenario.role) as env:
        initial = scenario.setup(env)
        outcome = agent.run(env, scenario)
        score = scenario.score(env, initial)
    try:
        from lightfall import __version__
    except Exception:  # noqa: BLE001
        __version__ = "unknown"
    return {
        "ts": time.time(), "scenario": scenario_name, "seed": seed,
        "lightfall": __version__, "agent": outcome.to_dict(), "score": score,
    }


def run_many(scenario_name: str, agent_args: list[str], seeds: list[str | None],
             out: Path) -> list[dict[str, Any]]:
    """Each run in a fresh subprocess (clean singletons, clean Qt), appended
    to ``out`` as one JSON line."""
    results = []
    out.parent.mkdir(parents=True, exist_ok=True)
    for seed in seeds:
        cmd = [sys.executable, "-m", "lightfall.eval", "_one", scenario_name, *agent_args,
               "--out", str(out)]
        if seed is not None:
            cmd += ["--seed", seed]
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        subprocess.run(cmd, env=env, check=False)
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                results.append(json.loads(line))
            except ValueError:
                pass
    return results


def summarize(results: list[dict[str, Any]]) -> str:
    """A small table: one row per run."""
    head = (f"{'scenario':<22} {'agent':<18} {'seed':<10} {'ok':<4} {'lift err':>9} "
            f"{'th err':>7} {'drift':>7} {'runs':>5} {'tools':>6} {'cost $':>7} {'min':>5}")
    rows = [head, "-" * len(head)]
    for r in results:
        s, a = r["score"], r["agent"]
        rows.append(
            f"{r['scenario']:<22} {a['agent'][:18]:<18} {str(r['seed'])[:10]:<10} "
            f"{'yes' if s.get('aligned', s.get('respected')) else 'no':<4} {s.get('lift_error_um', 0):>9.1f} "
            f"{s.get('theta_error_deg', 0):>7.3f} {s.get('translate_drift_um', 0):>7.1f} "
            f"{s.get('runs', 0):>5} {a.get('tool_call_count', 0):>6} {a.get('cost_usd', 0):>7.2f} "
            f"{a.get('wall_s', 0) / 60:>5.1f}"
        )
    return "\n".join(rows)
