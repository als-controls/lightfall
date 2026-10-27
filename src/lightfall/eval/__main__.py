"""``python -m lightfall.eval``: evaluate agents on the COSMIC sim beamline.

    python -m lightfall.eval list
    python -m lightfall.eval run reflection_alignment --agent scripted --seeds a,b,c
    python -m lightfall.eval run reflection_alignment --agent claude --model sonnet --seeds a,b
    python -m lightfall.eval summary results.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _agent(args):
    from lightfall.eval.agents import ClaudeAgentRunner, ScriptedAligner

    if args.agent == "scripted":
        return ScriptedAligner()
    return ClaudeAgentRunner(model=args.model, effort=args.effort,
                             max_turns=args.max_turns, timeout_s=args.timeout)


def _agent_args(args) -> list[str]:
    out = ["--agent", args.agent, "--max-turns", str(args.max_turns), "--timeout", str(args.timeout)]
    if args.model:
        out += ["--model", args.model]
    if args.effort:
        out += ["--effort", args.effort]
    return out


def main(argv: list[str] | None = None) -> int:
    from lightfall.eval.scenarios import SCENARIOS

    p = argparse.ArgumentParser(prog="python -m lightfall.eval", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List scenarios.")
    for name in ("run", "_one"):
        r = sub.add_parser(name, help="Run a scenario." if name == "run" else argparse.SUPPRESS)
        r.add_argument("scenario", choices=sorted(SCENARIOS))
        r.add_argument("--agent", choices=["scripted", "claude"], default="scripted")
        r.add_argument("--model", default=None)
        r.add_argument("--effort", default=None)
        r.add_argument("--max-turns", type=int, default=60)
        r.add_argument("--timeout", type=float, default=1800.0, help="Seconds per run.")
        r.add_argument("--out", type=Path, default=Path("eval-results.jsonl"))
        if name == "run":
            r.add_argument("--seeds", default="", help="Comma-separated sim seeds ('' = defaults).")
        else:
            r.add_argument("--seed", default=None)
    s = sub.add_parser("summary", help="Print a results file as a table.")
    s.add_argument("results", type=Path)
    args = p.parse_args(argv)

    from lightfall.eval.runner import run_many, run_one, summarize

    if args.cmd == "list":
        for name, cls in sorted(SCENARIOS.items()):
            print(f"{name}: {cls().description}")
        return 0
    if args.cmd == "summary":
        rows = [json.loads(x) for x in args.results.read_text(encoding="utf-8").splitlines() if x]
        print(summarize(rows))
        return 0
    if args.cmd == "_one":
        result = run_one(args.scenario, _agent(args), args.seed)
        with args.out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, default=str) + "\n")
        sys.stdout.flush()
        os._exit(0)  # Qt/NATS/agent threads must not keep the subprocess alive
    seeds = [x.strip() for x in args.seeds.split(",")] if args.seeds else [None]
    before = args.out.read_text(encoding="utf-8").count("\n") if args.out.exists() else 0
    results = run_many(args.scenario, _agent_args(args), seeds, args.out)[before:]
    print(summarize(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
