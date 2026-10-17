"""Opt-in live test against the real ``agy`` CLI (signed in via the OS keyring).

Run: LIGHTFALL_INTEGRATION=1 python -m pytest tests/agents/agy/test_live_agy.py -v
Never part of the default suite. Uses a temp agy home; the sign-in is untouched.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from lightfall.agents.backend import (
    PartialTextEvent,
    SessionConfig,
    TextBlockEvent,
    ToolCallEvent,
    TurnCancelledEvent,
    TurnErrorEvent,
    TurnResultEvent,
)
from lightfall.agents.backends.agy import home as agy_home
from lightfall.agents.backends.agy.backend import AgyCliBackend
from lightfall.agents.backends.agy.discover import find_agy
from lightfall.agents.tooling import ToolServer, ToolSpec

AGY = find_agy(None)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("LIGHTFALL_INTEGRATION") != "1",
                       reason="set LIGHTFALL_INTEGRATION=1 to run live agy tests"),
    pytest.mark.skipif(AGY is None, reason="agy CLI not found"),
]

PROJECT = "lbl-gemini-enterprise-access"
LOCATION = "global"


class _AllowPM:
    def is_auto_approved(self, tool):
        return True

    async def request_permission(self, tool, tool_input):
        return True, ""

    async def request_question(self, questions):
        return False, {}

    def cancel_all_pending(self):
        pass


async def _echo(args):
    return {"content": [{"type": "text", "text": str(args.get("text", ""))}]}


async def _run(backend, prompt, *, cancel_after_text=False, timeout=240):
    """Drive one turn; returns the events."""
    flag = {"cancel": False}
    events = []

    async def consume():
        async for ev in backend.run_turn(prompt, lambda: flag["cancel"], lambda: False):
            events.append(ev)
            if cancel_after_text and isinstance(ev, PartialTextEvent) and not flag["cancel"]:
                flag["cancel"] = True
                await backend.interrupt()

    await asyncio.wait_for(consume(), timeout)
    return events


def test_live_echo_cancel_resume(tmp_path, monkeypatch, capsys):
    root = tmp_path / "agy-root"
    root.mkdir()
    monkeypatch.setattr(agy_home, "lightfall_agy_home", lambda: root)
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "skills").mkdir()
    probe = ToolServer("probe", "1.0.0", [
        ToolSpec("echo", "Echo the given text back unchanged", {"text": str}, _echo)])
    cfg = SessionConfig(cwd=str(work), system_prompt="You are a terse test assistant.",
                        tool_servers=[("probe", probe)], external_servers={},
                        allowed_tools=[], subagents=[], skills_dir=tmp_path)
    backend = AgyCliBackend(cfg, _AllowPM(), False, agy=AGY, project=PROJECT, location=LOCATION)

    def text_of(events):
        out = "".join(e.text for e in events if isinstance(e, TextBlockEvent))
        return out or "".join(e.delta for e in events if isinstance(e, PartialTextEvent))

    def usage_of(events):
        return [e.info for e in events if isinstance(e, TurnResultEvent)]

    async def scenario():
        await backend.connect()
        try:
            t0 = time.time()
            ev1 = await _run(backend, "Call the echo tool of the MCP server named "
                                      "lightfall_probe with text 'kumquat', then reply "
                                      "with what it returned.")
            print(f"\nTURN1 ({time.time() - t0:.1f}s) usage={usage_of(ev1)}")
            print("TURN1 text:", text_of(ev1)[:200])
            errs = [e for e in ev1 if isinstance(e, TurnErrorEvent)]
            assert not errs, errs
            assert any(isinstance(e, ToolCallEvent) and e.name == "mcp__probe__echo" for e in ev1)
            assert "kumquat" in text_of(ev1).lower()

            ev2 = await _run(backend, "Count to 200 slowly, one number per line.",
                             cancel_after_text=True)
            print("TURN2 events:", [type(e).__name__ for e in ev2][-6:])
            assert any(isinstance(e, TurnCancelledEvent) for e in ev2)

            ev3 = await _run(backend, "What word did the echo tool return?")
            print(f"TURN3 usage={usage_of(ev3)}")
            print("TURN3 text:", text_of(ev3)[:200])
            assert not [e for e in ev3 if isinstance(e, TurnErrorEvent)]
            assert "kumquat" in text_of(ev3).lower()
        finally:
            await backend.close()

    asyncio.run(scenario())
