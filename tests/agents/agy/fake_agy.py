"""Stand-in for the ``agy`` CLI in tests: ``[sys.executable, fake_agy.py, ...]``.

Reads stream-json ``user`` lines on stdin; for each one emits the scenario named
by ``FAKE_AGY_SCENARIO`` (text | tool | die | slow | deaf; deaf = slow that
ignores interrupts). Each start appends one JSON
line ``{"argv", "pid", "env"}`` to ``FAKE_AGY_ARGV_LOG`` when that is set.
Never runs hooks.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
import uuid

argv = sys.argv[1:]
CID = argv[argv.index("--conversation") + 1] if "--conversation" in argv else str(uuid.uuid4())
_totals = {"input_tokens": 0, "output_tokens": 0}
_step = 0


def _log_start() -> None:
    path = os.environ.get("FAKE_AGY_ARGV_LOG")
    if not path:
        return
    env = {k: os.environ.get(k) for k in ("LF_AGY_PORT", "LF_AGY_TOKEN", "USERPROFILE", "HOME")}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "pid": os.getpid(), "env": env}) + "\n")


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def step(state: str, step_type: str, **extra) -> None:
    emit({"event": "step_update", "step_update": {
        "conversation_id": CID, "step_index": _step, "state": state, "step_type": step_type,
        **extra}})


def next_step() -> None:
    global _step
    _step += 1


def result(status: str = "SUCCESS", response: str = "", error: str | None = None,
           inp: int = 100, out: int = 10) -> None:
    _totals["input_tokens"] += inp
    _totals["output_tokens"] += out
    r = {"conversation_id": CID, "status": status, "response": response, "num_turns": 1,
         "usage": {**_totals, "thinking_tokens": 0, "cache_read_tokens": 0,
                   "total_tokens": sum(_totals.values())}}
    if error is not None:
        r["error"] = error
    emit({"event": "result", "result": r})


def begin_turn() -> None:
    step("DONE", "user_input")
    next_step()


def text_reply(parts: list[str]) -> None:
    for i, p in enumerate(parts):
        step("DONE" if i == len(parts) - 1 else "ACTIVE", "agent_response", text_delta=p)
    next_step()


def scenario_text() -> None:
    begin_turn()
    text_reply(["Hello ", "world"])
    result(response="Hello world")


def scenario_tool() -> None:
    begin_turn()
    info = {"name": "call_mcp_tool", "parameters": {
        "Arguments": {"region": "full"}, "ServerName": "lightfall_qt", "ToolName": "screenshot"}}
    step("ACTIVE", "tool", tool_name="call_mcp_tool", tool_info=info)
    step("DONE", "tool", tool_name="call_mcp_tool", tool_info={**info, "output": "image saved"})
    next_step()
    text_reply(["Took a screenshot."])
    result(response="Took a screenshot.")


def scenario_die() -> None:
    begin_turn()
    step("ACTIVE", "agent_response", text_delta="Partial")
    sys.stderr.write('AGY_ERROR: {"code": "INTERNAL", "message": "fake crash"}\n')
    sys.stderr.flush()
    sys.exit(3)


def _on_interrupt(signum, frame) -> None:
    result(status="ERROR", error="interrupted")
    sys.exit(0)


def scenario_slow() -> None:
    begin_turn()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        step("ACTIVE", "agent_response", text_delta="tick ")
        time.sleep(0.2)
    step("DONE", "agent_response")
    next_step()
    result(response="tick")


SCENARIOS = {"text": scenario_text, "tool": scenario_tool, "die": scenario_die,
             "slow": scenario_slow, "deaf": scenario_slow}


def main() -> None:
    _log_start()
    name = os.environ.get("FAKE_AGY_SCENARIO", "text")
    handler = signal.SIG_IGN if name == "deaf" else _on_interrupt
    signal.signal(getattr(signal, "SIGBREAK", signal.SIGINT), handler)
    emit({"event": "init", "conversation_id": CID,
          "init": {"cwd": os.getcwd(), "tools": ["call_mcp_tool"], "permission_mode": "default"}})
    scenario = SCENARIOS[name]
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("event") == "user":
            scenario()


if __name__ == "__main__":
    main()
