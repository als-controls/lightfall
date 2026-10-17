"""Stand-in for the ``agy`` CLI in tests: ``[sys.executable, fake_agy.py, ...]``.

Reads stream-json ``user`` lines on stdin; for each one emits the scenario named
by ``FAKE_AGY_SCENARIO``: text | tool | die | slow | deaf (slow that ignores
interrupts) | text_exit (text, then exit while idle) | text_mute (text, then
exit silently on the next message, as if it had died while idle) | text_hook_mute
(like text_mute, but first POSTs to the host's /hook) | text_partial_die (text,
then on the next message user_input + one delta and exit). Each user message is
appended as ``{"pid", "prompt"}`` to ``FAKE_AGY_PROMPT_LOG`` when that is set. Each start appends one JSON
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


def _log_prompt(msg: dict) -> None:
    path = os.environ.get("FAKE_AGY_PROMPT_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(),
                                "prompt": msg.get("message", {}).get("content")}) + "\n")


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


_turns = 0


def scenario_text_exit() -> None:
    scenario_text()
    sys.exit(0)


def scenario_text_mute() -> None:
    global _turns
    _turns += 1
    if _turns > 1:
        os._exit(0)  # no output at all for this message
    scenario_text()


def _post_hook() -> None:
    import urllib.request

    body = json.dumps({"conversationId": CID, "stepIdx": _step, "toolCall": {
        "name": "call_mcp_tool",
        "args": {"ServerName": "lightfall_qt", "ToolName": "screenshot", "Arguments": {}}}})
    req = urllib.request.Request(
        f"http://127.0.0.1:{os.environ['LF_AGY_PORT']}/hook", data=body.encode(),
        headers={"Authorization": f"Bearer {os.environ['LF_AGY_TOKEN']}",
                 "Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.open(req, timeout=10).read()


def scenario_text_hook_mute() -> None:
    global _turns
    _turns += 1
    if _turns > 1:
        _post_hook()  # reached Lightfall, then died without a word on stdout
        os._exit(0)
    scenario_text()


def scenario_text_partial_die() -> None:
    global _turns
    _turns += 1
    if _turns > 1:
        begin_turn()
        step("ACTIVE", "agent_response", text_delta="Part")
        os._exit(0)
    scenario_text()


SCENARIOS = {"text": scenario_text, "tool": scenario_tool, "die": scenario_die,
             "slow": scenario_slow, "deaf": scenario_slow, "text_exit": scenario_text_exit,
             "text_mute": scenario_text_mute, "text_hook_mute": scenario_text_hook_mute,
             "text_partial_die": scenario_text_partial_die}


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
            _log_prompt(msg)
            scenario()


if __name__ == "__main__":
    main()
