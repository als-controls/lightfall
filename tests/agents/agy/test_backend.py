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

from lightfall.agents.backend import SessionConfig, TurnCancelledEvent, TurnErrorEvent
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
    for var in (
        "NO_PROXY", "no_proxy",
        "HTTP_PROXY", "http_proxy",
        "HTTPS_PROXY", "https_proxy",
        "ALL_PROXY", "all_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
    root = tmp_path / "agy-root"
    root.mkdir()
    monkeypatch.setattr(agy_home, "lightfall_agy_home", lambda: root)
    log = tmp_path / "argv.ndjson"
    monkeypatch.setenv("FAKE_AGY_ARGV_LOG", str(log))
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "text")
    prompts = tmp_path / "prompts.ndjson"
    monkeypatch.setenv("FAKE_AGY_PROMPT_LOG", str(prompts))
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    cfg = SessionConfig(cwd=str(work), system_prompt="You are a test.", tool_servers=_servers(),
                        external_servers={}, allowed_tools=[], subagents=[], skills_dir=tmp_path)
    return {"root": root, "log": log, "prompts": prompts, "cfg": cfg,
            "monkeypatch": monkeypatch}


def _backend(env, pm=None, require_approval=False):
    return AgyCliBackend(env["cfg"], pm or _FakePM(), require_approval, agy=AGY,
                         project="test-project", location="test-loc")


def _starts(env):
    if not env["log"].exists():
        return []
    return [json.loads(x) for x in env["log"].read_text(encoding="utf-8").splitlines() if x]


def _prompts(env):
    if not env["prompts"].exists():
        return []
    return [json.loads(x)["prompt"]
            for x in env["prompts"].read_text(encoding="utf-8").splitlines() if x]


def _start_worker(qtbot, backend, pm=None):
    w = AgentWorker(backend, permission_manager=pm)
    with qtbot.waitSignal(w.connected, timeout=10000):
        w.start()
    return w


def _query(qtbot, w, prompt, signal, timeout=15000):
    with qtbot.waitSignal(signal, timeout=timeout):
        w.send_query(prompt)
    qtbot.waitUntil(lambda: not w.is_processing, timeout=5000)


def _record_partials(w):
    rec = {"started": [], "text": []}
    w.partial_block_started.connect(lambda bid, kind: rec["started"].append(bid))
    w.partial_text.connect(lambda bid, d: rec["text"].append(d))
    return rec


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
        # the rules name the real (plugin-prefixed) servers and their tools
        rules = (plugin / "rules" / "AGENTS.md").read_text(encoding="utf-8")
        assert rules.startswith("You are a test.")
        assert "`lightfall_qt`" in rules and "`lightfall_ask`" in rules
        assert "`screenshot` — Take a screenshot" in rules and "`ask_user`" in rules
        assert str(home / ".gemini" / "antigravity-cli" / "mcp") in rules
        operator = (plugin / "agents" / "operator.md").read_text(encoding="utf-8")
        assert "run_command" in operator and "`lightfall_qt`" in operator
        assert (plugin / "skills" / "demo" / "SKILL.md").is_file()
        hook = json.loads((plugin / "hooks.json").read_text(encoding="utf-8"))
        assert " -I " in (
            hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        assert "hook_client.py" in hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert '"' not in hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
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
        rec = _record_partials(w)
        _query(qtbot, w, "second", w.query_completed)
        # the respawned child reuses step indexes under the same conversation id
        assert len(rec["started"]) == 1 and "".join(rec["text"]) == "Hello world"
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
        # interrupt (Windows: immediate terminate; POSIX: SIGINT -> interrupted result),
        # not the 5 s kill fallback, ended the turn, and the child is gone
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
        rec = _record_partials(w)
        _query(qtbot, w, "two", w.query_completed)
        assert len(rec["started"]) == 1 and "".join(rec["text"]) == "Hello world"
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


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows interrupt terminates the child; no grace period applies")
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


@pytest.mark.parametrize("scenario", ["text_exit", "text_mute"])
def test_child_dead_between_turns_respawns_transparently(qtbot, env, scenario):
    # text_exit: child exits while idle; text_mute: child dies silently on the next
    # prompt (death not yet noticed at send time) -> one respawn, no error.
    env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", scenario)
    b = _backend(env)
    w = _start_worker(qtbot, b)
    errors = []
    w.error_occurred.connect(errors.append)
    try:
        _query(qtbot, w, "one", w.query_completed)
        cid = b.conversation_id
        rec = _record_partials(w)
        _query(qtbot, w, "two", w.query_completed)
        assert errors == []
        assert "".join(rec["text"]) == "Hello world"
        if scenario == "text_mute":
            assert _prompts(env) == ["one", "two", "two"]  # resent to the new child
        first, second = _starts(env)
        assert second["argv"][second["argv"].index("--conversation") + 1] == cid
    finally:
        _stop(w)


def test_windows_child_spawned_without_console(qtbot, env, monkeypatch):
    from lightfall.agents.backends.agy import process as agy_process

    seen = {}
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        seen.update(kwargs)
        return await real(*args, **kwargs)

    monkeypatch.setattr(agy_process.asyncio, "create_subprocess_exec", spy)
    w = _start_worker(qtbot, _backend(env))
    try:
        _query(qtbot, w, "hi", w.query_completed)
        (start,) = _starts(env)
        assert seen["env"]["NO_PROXY"] == "127.0.0.1,localhost"
        if sys.platform == "win32":
            import subprocess
            flags = seen["creationflags"]
            assert flags & subprocess.CREATE_NO_WINDOW
            assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
    finally:
        _stop(w)


@pytest.mark.parametrize("scenario", ["text_hook_mute", "text_partial_die"])
def test_no_resend_when_child_may_have_acted(qtbot, env, scenario):
    # text_hook_mute: the child reached the tool host before dying silently;
    # text_partial_die: it emitted events before dying. Either way the prompt may
    # have had side effects, so it is NOT resent: one error, prompt seen once.
    env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", scenario)
    w = _start_worker(qtbot, _backend(env))
    errors, completed = [], []
    w.error_occurred.connect(errors.append)
    w.query_completed.connect(lambda: completed.append(1))
    try:
        _query(qtbot, w, "one", w.query_completed)
        _query(qtbot, w, "two", w.error_occurred)
        qtbot.wait(300)
        assert len(errors) == 1 and errors[0].startswith("agy exited unexpectedly")
        assert completed == [1]
        assert _prompts(env) == ["one", "two"]
        assert len(_starts(env)) == 1
    finally:
        _stop(w)


def test_cancel_before_retry_spawn_is_cancelled(env):
    env["monkeypatch"].setenv("FAKE_AGY_SCENARIO", "text_mute")
    b = _backend(env)

    async def go():
        await b.connect()
        try:
            first = [e async for e in b.run_turn("one", lambda: False, lambda: False)]
            await asyncio.sleep(0.2)
            # the user cancels once the dead child has been reaped (before any respawn)
            second = [e async for e in b.run_turn("two", lambda: b._proc is None,
                                                  lambda: False)]
            return first, second
        finally:
            await b.close()

    first, second = asyncio.run(go())
    assert type(first[-2]).__name__ == "TurnResultEvent"
    assert second == [TurnCancelledEvent()]
    assert _prompts(env) == ["one", "two"]
    assert len(_starts(env)) == 1


def test_cancel_seen_only_at_retry_point_does_not_respawn(env, monkeypatch):
    # Pins the guard in _turn itself: the stream reports "retry" while cancel is
    # already requested -> TurnCancelled, no second spawn or resend.
    from lightfall.agents.backends.agy import backend as agy_backend

    b = _backend(env)
    calls = {"start": 0, "send": 0}
    real_start, real_send = agy_backend.AgyProcess.start, agy_backend.AgyProcess.send

    async def start(self):
        calls["start"] += 1
        await real_start(self)

    async def send(self, text):
        calls["send"] += 1
        await real_send(self, text)

    monkeypatch.setattr(agy_backend.AgyProcess, "start", start)
    monkeypatch.setattr(agy_backend.AgyProcess, "send", send)

    async def fake_stream(proc, cancelled, stopping, *, retryable, untouched):
        yield agy_backend._RETRY

    async def go():
        await b.connect()
        try:
            await b._ensure_child()  # a live, reused child
            monkeypatch.setattr(b, "_stream", fake_stream)
            return [e async for e in b.run_turn("two", lambda: True, lambda: False)]
        finally:
            await b.close()

    assert asyncio.run(go()) == [TurnCancelledEvent()]
    assert calls == {"start": 1, "send": 1}


def test_no_proxy_merges_existing_value(monkeypatch):
    from lightfall.agents.backends.agy import backend as agy_backend

    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "lbl.gov, localhost")
    env = agy_backend._no_proxy_env()
    assert env["NO_PROXY"] == "lbl.gov,localhost,127.0.0.1"
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert agy_backend._no_proxy_env()["NO_PROXY"] == "127.0.0.1,localhost"


def test_two_sessions_isolated(qtbot, env):
    b1, b2 = _backend(env), _backend(env)
    w1 = _start_worker(qtbot, b1)
    w2 = _start_worker(qtbot, b2)
    try:
        assert b1.session_home != b2.session_home
        _query(qtbot, w1, "one", w1.query_completed)
        _query(qtbot, w2, "two", w2.query_completed)
        starts = _starts(env)
        assert len(starts) == 2
        envs = [s["env"] for s in starts]
        assert all(e["LF_AGY_PORT"] and e["LF_AGY_TOKEN"] for e in envs)
        assert len({e["LF_AGY_PORT"] for e in envs}) == 2
        assert len({e["LF_AGY_TOKEN"] for e in envs}) == 2
        assert {e["USERPROFILE"] for e in envs} == {str(b1.session_home), str(b2.session_home)}
    finally:
        _stop(w1)
        _stop(w2)


def test_connect_failure_before_resources_leaves_nothing(env, monkeypatch):
    from lightfall.agents.backends.agy import backend as backend_mod

    def boom(*a, **k):
        raise ValueError("bad hook path")

    monkeypatch.setattr(backend_mod, "hook_command", boom)
    b = _backend(env)
    with pytest.raises(ValueError, match="bad hook path"):
        asyncio.run(b.connect())
    assert b._host is None and b.session_home is None
    assert not (env["root"] / "lightfall" / "sessions").exists() or not any(
        (env["root"] / "lightfall" / "sessions").iterdir())


def test_connect_failure_after_host_start_cleans_up(env, monkeypatch):
    from lightfall.agents.backends.agy import backend as backend_mod

    seen = {}

    def boom(home, cfg):
        seen["home"], seen["port"] = home, cfg.port
        raise OSError("disk full")

    monkeypatch.setattr(backend_mod, "write_session_config", boom)
    b = _backend(env)
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(b.connect())
    assert b._host is None and b.session_home is None
    assert not seen["home"].exists()
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", seen["port"]), timeout=2):
        pass


def _with_subagents(env, subagents):
    import dataclasses

    env["cfg"] = dataclasses.replace(env["cfg"], subagents=subagents)


def test_connect_writes_subagent_files(env, monkeypatch):
    from lightfall.agents.backend import SubagentSpec
    from lightfall.agents.backends.agy import backend as backend_mod

    probed = []
    monkeypatch.setattr(backend_mod, "agy_model_ids",
                        lambda agy: probed.append(agy) or frozenset({"gemini-x"}))
    _with_subagents(env, [SubagentSpec("echoer", "Echoes.", "You echo.", model="gemini-x"),
                          SubagentSpec("saxs", "SAXS.", "You reduce.", model="sonnet")])
    b = _backend(env)
    asyncio.run(b.connect())
    try:
        agents = b.session_home / ".gemini" / "config" / "plugins" / "lightfall" / "agents"
        assert sorted(p.name for p in agents.iterdir()) == ["echoer.md", "operator.md", "saxs.md"]
        assert "model: gemini-x\n" in (agents / "echoer.md").read_text(encoding="utf-8")
        assert "model: inherit\n" in (agents / "saxs.md").read_text(encoding="utf-8")
        assert probed == [AGY]
    finally:
        asyncio.run(b.close())


def test_connect_skips_model_probe_when_no_subagent_needs_it(env, monkeypatch):
    from lightfall.agents.backend import SubagentSpec
    from lightfall.agents.backends.agy import backend as backend_mod

    def boom(agy):
        raise AssertionError("agy models must not run")

    monkeypatch.setattr(backend_mod, "agy_model_ids", boom)
    _with_subagents(env, [SubagentSpec("echoer", "Echoes.", "You echo."),
                          SubagentSpec("fast", "Fast.", "Quick.", model="flash")])
    b = _backend(env)
    asyncio.run(b.connect())
    asyncio.run(b.close())


def test_connect_survives_model_probe_failure(env, monkeypatch):
    from lightfall.agents.backend import SubagentSpec
    from lightfall.agents.backends.agy import backend as backend_mod

    def boom(agy):
        raise RuntimeError("not signed in")

    monkeypatch.setattr(backend_mod, "agy_model_ids", boom)
    _with_subagents(env, [SubagentSpec("echoer", "Echoes.", "You echo.", model="gemini-x")])
    b = _backend(env)
    asyncio.run(b.connect())
    try:
        agents = b.session_home / ".gemini" / "config" / "plugins" / "lightfall" / "agents"
        assert "model: inherit\n" in (agents / "echoer.md").read_text(encoding="utf-8")
    finally:
        asyncio.run(b.close())
