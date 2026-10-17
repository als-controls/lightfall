"""AgyCliBackend driven by AgentWorker against tests/agents/agy/fake_agy.py.

A real ToolHost serves the tools; fake_agy stands in for the agy CLI (it never
runs hooks, so the hook path is exercised by posting to /hook directly).
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil
import pytest

from lightfall.agents.backend import SessionConfig, TurnErrorEvent
from lightfall.agents.backends.agy import home as agy_home
from lightfall.agents.backends.agy.backend import AgyCliBackend
from lightfall.agents.tooling import ToolServer, ToolSpec
from lightfall.agents.worker import AgentWorker
from lightfall.claude.permission_manager import PermissionManager

FAKE = Path(__file__).with_name("fake_agy.py")
AGY = [sys.executable, str(FAKE)]


async def _shot(args):
    return {"content": [{"type": "text", "text": "image saved"}]}


async def _move(args):
    return {"content": [{"type": "text", "text": "moved"}]}


def _servers():
    return [("qt", ToolServer("qt", "1.0.0", [
        ToolSpec("screenshot", "Take a screenshot", {}, _shot),
        ToolSpec("move", "Move a motor", {"motor": str}, _move),
    ]))]


class _FakePM:
    def __init__(self):
        self.cancelled = 0

    def is_auto_approved(self, tool):
        return True

    async def request_permission(self, tool, tool_input):
        return True, ""

    async def request_question(self, questions):
        return False, {}

    def cancel_all_pending(self):
        self.cancelled += 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "agy-root"
    root.mkdir()
    monkeypatch.setattr(agy_home, "lightfall_agy_home", lambda: root)
    log = tmp_path / "argv.ndjson"
    monkeypatch.setenv("FAKE_AGY_ARGV_LOG", str(log))
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "text")
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    cfg = SessionConfig(cwd=str(work), system_prompt="You are a test.", tool_servers=_servers(),
                        external_servers={}, allowed_tools=[], subagents=[], skills_dir=tmp_path)
    return {"root": root, "log": log, "cfg": cfg, "monkeypatch": monkeypatch}


def _backend(env, pm=None, require_approval=False):
    return AgyCliBackend(env["cfg"], pm or _FakePM(), require_approval, agy=AGY,
                         project="test-project", location="test-loc")


def _starts(env):
    if not env["log"].exists():
        return []
    return [json.loads(x) for x in env["log"].read_text(encoding="utf-8").splitlines() if x]


def _start_worker(qtbot, backend, pm=None):
    w = AgentWorker(backend, permission_manager=pm)
    with qtbot.waitSignal(w.connected, timeout=10000):
        w.start()
    return w


def _query(qtbot, w, prompt, signal, timeout=15000):
    with qtbot.waitSignal(signal, timeout=timeout):
        w.send_query(prompt)
    qtbot.waitUntil(lambda: not w.is_processing, timeout=5000)


def _stop(w):
    w.stop()
    assert w.wait(15000)


def test_text_turn_streams_and_completes(qtbot, env):
    b = _backend(env)
    w = _start_worker(qtbot, b)
    started, text, finished, results, sids, errors = [], [], [], [], [], []
    w.partial_block_started.connect(lambda bid, kind: started.append((bid, kind)))
    w.partial_text.connect(lambda bid, d: text.append(d))
    w.partial_block_finished.connect(finished.append)
    w.result_received.connect(results.append)
    w.session_id_changed.connect(sids.append)
    w.error_occurred.connect(errors.append)
    try:
        home = b.session_home
        _query(qtbot, w, "hi", w.query_completed)
        qtbot.waitUntil(lambda: len(sids) == 1, timeout=2000)
        assert errors == []
        assert len(started) == 1 and started[0][1] == "text"
        assert "".join(text) == "Hello world"
        assert finished == [started[0][0]]
        assert results[0]["input_tokens"] == 100 and results[0]["output_tokens"] == 10
        assert results[0]["total_cost_usd"] is None
        assert sids == [results[0]["session_id"]] == [b.conversation_id]

        (start,) = _starts(env)
        assert start["argv"][:2] == ["--agent", "operator"]
        assert "--conversation" not in start["argv"] and "--model" not in start["argv"]
        # session isolation: the child sees the session home and this session's host
        assert Path(start["env"]["USERPROFILE"]) == home == Path(start["env"]["HOME"])
        assert start["env"]["LF_AGY_PORT"] == str(b._host.port)
        assert start["env"]["LF_AGY_TOKEN"] == b._token
        plugin = home / ".gemini" / "config" / "plugins" / "lightfall"
        mcp = json.loads((plugin / "mcp_config.json").read_text(encoding="utf-8"))
        assert set(mcp["mcpServers"]) == {"qt", "ask"}
        assert (plugin / "skills" / "demo" / "SKILL.md").is_file()
        hook = json.loads((plugin / "hooks.json").read_text(encoding="utf-8"))
        assert "-I -m lightfall.agents.backends.agy.hook_client" in (
            hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        settings = json.loads(
            (env["root"] / "lightfall" / ".gemini" / "antigravity-cli" / "settings.json")
            .read_text(encoding="utf-8"))
        assert settings["gcp"] == {"project": "test-project", "location": "test-loc"}
        assert "mcp(lightfall_ask/*)" in settings["permissions"]["allow"]
        assert "mcp(lightfall_qt/*)" in settings["permissions"]["allow"]
    finally:
        _stop(w)


def test_tool_turn_maps_tool_events(qtbot, env):
    env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", "tool")
    w = _start_worker(qtbot, _backend(env))
    calls, results = [], []
    w.tool_called.connect(lambda name, inp: calls.append((name, inp)))
    w.tool_result.connect(lambda tid, payload: results.append(payload))
    try:
        _query(qtbot, w, "shoot", w.query_completed)
        assert calls == [("mcp__qt__screenshot", {"region": "full"})]
        assert results == [{"content": "image saved", "is_error": False}]
    finally:
        _stop(w)


def test_resume_passes_conversation(qtbot, env):
    env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", "die")
    b = _backend(env)
    w = _start_worker(qtbot, b)
    try:
        _query(qtbot, w, "first", w.error_occurred)
        cid = b.conversation_id
        assert cid
        env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", "text")
        results = []
        w.result_received.connect(results.append)
        _query(qtbot, w, "second", w.query_completed)
        first, second = _starts(env)
        assert "--conversation" not in first["argv"]
        i = second["argv"].index("--conversation")
        assert second["argv"][i + 1] == cid
        assert results[0]["session_id"] == cid
    finally:
        _stop(w)


def test_resume_from_config_passes_conversation(qtbot, env):
    env["cfg"].resume = "prev-conv"
    w = _start_worker(qtbot, _backend(env))
    try:
        _query(qtbot, w, "hi", w.query_completed)
        (start,) = _starts(env)
        i = start["argv"].index("--conversation")
        assert start["argv"][i + 1] == "prev-conv"
    finally:
        _stop(w)


def test_child_exit_mid_turn_yields_error_then_respawns(qtbot, env):
    env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", "die")
    w = _start_worker(qtbot, _backend(env))
    errors, completed = [], []
    w.error_occurred.connect(errors.append)
    w.query_completed.connect(lambda: completed.append(1))
    try:
        _query(qtbot, w, "first", w.error_occurred)
        env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", "text")
        _query(qtbot, w, "second", w.query_completed)
        assert len(errors) == 1
        assert errors[0].startswith("agy exited unexpectedly:")
        assert "AGY_ERROR" in errors[0]
        assert completed == [1]
        assert len(_starts(env)) == 2
    finally:
        _stop(w)


def test_cancel_during_pending_hook(qtbot, env):
    env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", "slow")
    pm = PermissionManager()
    b = _backend(env, pm=pm, require_approval=True)
    w = _start_worker(qtbot, b, pm=pm)
    cancelled, completed, errors = [], [], []
    w.query_cancelled.connect(lambda: cancelled.append(1))
    w.query_completed.connect(lambda: completed.append(1))
    w.error_occurred.connect(errors.append)
    hook_reply = {}

    def _post_hook():
        payload = {"conversationId": "x", "stepIdx": 3, "toolCall": {
            "name": "call_mcp_tool",
            "args": {"ServerName": "lightfall_qt", "ToolName": "move",
                     "Arguments": {"motor": "sx"}}}}
        with httpx.Client(trust_env=False, timeout=30) as c:
            r = c.post(f"http://127.0.0.1:{b._host.port}/hook", json=payload,
                       headers={"Authorization": f"Bearer {b._token}"})
            hook_reply.update(r.json())

    try:
        with qtbot.waitSignal(w.partial_text, timeout=10000):
            w.send_query("go slowly")
        t = threading.Thread(target=_post_hook, daemon=True)
        with qtbot.waitSignal(pm.permission_requested, timeout=10000):
            t.start()
        t0 = time.monotonic()
        with qtbot.waitSignal(w.query_cancelled, timeout=10000):
            assert w.cancel_current_query()
        elapsed = time.monotonic() - t0
        t.join(10)
        qtbot.waitUntil(lambda: not w.is_processing, timeout=5000)
        qtbot.wait(300)
        assert cancelled == [1] and completed == [] and errors == []
        assert hook_reply == {"decision": "deny", "reason": "Query cancelled by user"}
        # interrupt (not the 5 s kill fallback) ended the turn, and the child is gone
        assert elapsed < 4.0
        assert b._proc is None
        (start,) = _starts(env)
        assert not psutil.pid_exists(start["pid"]) or (
            psutil.Process(start["pid"]).status() == psutil.STATUS_ZOMBIE)
    finally:
        _stop(w)


def test_set_model_respawns_with_model(qtbot, env):
    b = _backend(env)
    w = _start_worker(qtbot, b)
    try:
        _query(qtbot, w, "one", w.query_completed)
        cid = b.conversation_id
        w.request_set_model("gemini-test-model")
        qtbot.waitUntil(lambda: b._proc is None, timeout=8000)
        _query(qtbot, w, "two", w.query_completed)
        first, second = _starts(env)
        assert "--model" not in first["argv"]
        argv = second["argv"]
        assert argv[argv.index("--model") + 1] == "gemini-test-model"
        assert argv[argv.index("--conversation") + 1] == cid
    finally:
        _stop(w)


def test_close_kills_child_and_stops_host(qtbot, env):
    b = _backend(env)
    w = _start_worker(qtbot, b)
    _query(qtbot, w, "hi", w.query_completed)
    port, home = b._host.port, b.session_home
    (start,) = _starts(env)
    assert psutil.pid_exists(start["pid"])
    _stop(w)
    assert not psutil.pid_exists(start["pid"]) or (
        psutil.Process(start["pid"]).status() == psutil.STATUS_ZOMBIE)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    assert not home.exists()
    # the shared per-spec store survives the session home's removal
    assert (env["root"] / "lightfall" / ".gemini" / "antigravity-cli" / "settings.json").is_file()


def test_close_without_connect_is_safe(env):
    b = _backend(env)
    asyncio.run(b.close())
    asyncio.run(b.close())


def test_run_turn_before_connect_is_error(env):
    b = _backend(env)

    async def go():
        return [e async for e in b.run_turn("hi", lambda: False, lambda: False)]

    assert asyncio.run(go()) == [TurnErrorEvent("agy backend is not connected")]


def test_cancel_kills_child_that_ignores_interrupt(qtbot, env):
    env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", "deaf")
    b = _backend(env)
    w = _start_worker(qtbot, b)
    cancelled, completed, errors = [], [], []
    w.query_cancelled.connect(lambda: cancelled.append(1))
    w.query_completed.connect(lambda: completed.append(1))
    w.error_occurred.connect(errors.append)
    try:
        with qtbot.waitSignal(w.partial_text, timeout=10000):
            w.send_query("go slowly")
        t0 = time.monotonic()
        with qtbot.waitSignal(w.query_cancelled, timeout=15000):
            assert w.cancel_current_query()
        assert time.monotonic() - t0 >= 4.5  # the 5 s grace, then kill
        qtbot.waitUntil(lambda: not w.is_processing, timeout=5000)
        assert cancelled == [1] and completed == [] and errors == []
        assert b._proc is None
        (start,) = _starts(env)
        assert not psutil.pid_exists(start["pid"]) or (
            psutil.Process(start["pid"]).status() == psutil.STATUS_ZOMBIE)
    finally:
        _stop(w)
