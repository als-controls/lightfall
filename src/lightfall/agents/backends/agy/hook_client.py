"""Stdlib-only PreToolUse hook command run by agy.

Run as ``python -I -m lightfall.agents.backends.agy.hook_client``. Forwards the
hook payload from stdin to the session's Lightfall host and prints its decision.
On ANY failure it prints a deny decision and exits 0 (fail closed).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

_TIMEOUT_S = 285


def _forward(raw: bytes) -> str:
    port = os.environ["LF_AGY_PORT"]
    token = os.environ["LF_AGY_TOKEN"]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/hook", data=raw, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    # Never honour proxy env/registry settings: the token and payload must stay on loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=_TIMEOUT_S) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        body = resp.read().decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict) or "decision" not in parsed:
        raise ValueError("response has no decision")
    return body


def main() -> int:
    try:
        out = _forward(sys.stdin.buffer.read())
    except BaseException as e:  # noqa: BLE001 - fail closed on anything
        out = json.dumps({"decision": "deny", "reason": f"Lightfall unreachable: {e!r}"})
    sys.stdout.write(out)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
