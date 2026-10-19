"""BackendInfo descriptors: caps, auth status, model lists (no real agy/network)."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from lightfall.agents.backends import info
from lightfall.agents.backends.agy import discover


@pytest.fixture(autouse=True)
def _clear(monkeypatch, tmp_path):
    from lightfall.agents.backends.agy import home

    monkeypatch.setattr(home, "spec_home", lambda name: tmp_path)  # never touch the real agy home
    info.clear_caches()
    yield
    info.clear_caches()


def _fake_run(stdout="", stderr="", rc=0, exc=None):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if exc is not None:
            raise exc
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)

    return run, calls


ROWS = "Fetching available models...\ngemini-3-pro\tGemini 3 Pro\ngemini-3-flash\tGemini 3 Flash\n"


def test_agy_models_parses_tab_rows(monkeypatch, tmp_path):
    run, _ = _fake_run(stdout=ROWS)
    monkeypatch.setattr(discover.subprocess, "run", run)
    rows, detail = discover.agy_models("agy", tmp_path)
    assert rows == [("gemini-3-pro", "Gemini 3 Pro"), ("gemini-3-flash", "Gemini 3 Flash")]
    assert detail == "2 models"


def test_agy_models_failure_returns_none(monkeypatch, tmp_path):
    run, _ = _fake_run(stderr="not authenticated\n", rc=1)
    monkeypatch.setattr(discover.subprocess, "run", run)
    rows, detail = discover.agy_models("agy", tmp_path)
    assert rows is None and detail == "not authenticated"


def test_agy_signed_in_still_works(monkeypatch, tmp_path):
    run, _ = _fake_run(stdout=ROWS)
    monkeypatch.setattr(discover.subprocess, "run", run)
    assert discover.agy_signed_in("agy", tmp_path) == (True, "2 models")


def test_agy_models_timeout(monkeypatch, tmp_path):
    run, _ = _fake_run(exc=subprocess.TimeoutExpired("agy", 30))
    monkeypatch.setattr(discover.subprocess, "run", run)
    rows, detail = discover.agy_models("agy", tmp_path)
    assert rows is None and "timed out" in detail


def _agy_found(monkeypatch, path="C:/x/agy.exe"):
    monkeypatch.setattr(discover, "find_agy", lambda pref: path)


def test_agy_info_caps_and_names():
    a = info.AgyInfo()
    assert (a.kind, a.display_name, a.model_pref_key) == ("agy", "Gemini", "agy_model")
    assert a.caps == info.BackendCaps(cost=False, live_set_model=False, subagents=True)
    c = info.ClaudeCodeInfo()
    assert (c.kind, c.display_name, c.model_pref_key) == ("claude-code", "Claude", "claude_model")
    assert c.caps == info.BackendCaps(cost=True, live_set_model=True, subagents=True)


def test_agy_auth_ok_and_models(monkeypatch):
    _agy_found(monkeypatch)
    run, calls = _fake_run(stdout=ROWS)
    monkeypatch.setattr(discover.subprocess, "run", run)
    a = info.AgyInfo()
    assert a.auth_status() == info.AuthStatus(True, "Signed in (2 models)")
    models = a.list_models()
    assert models[0] == info.ModelOption("", "Default (agy)")
    assert [m.id for m in models[1:]] == ["gemini-3-pro", "gemini-3-flash"]
    assert len(calls) == 1  # second call served from the TTL cache
    assert [m.id for m in a.cached_models()] == ["", "gemini-3-pro", "gemini-3-flash"]


def test_agy_refresh_bypasses_cache(monkeypatch):
    _agy_found(monkeypatch)
    run, calls = _fake_run(stdout=ROWS)
    monkeypatch.setattr(discover.subprocess, "run", run)
    a = info.AgyInfo()
    a.auth_status()
    a.auth_status(refresh=True)
    assert len(calls) == 2


def test_agy_not_found(monkeypatch):
    monkeypatch.setattr(discover, "find_agy", lambda pref: None)
    a = info.AgyInfo()
    assert a.auth_status() == info.AuthStatus(False, "agy (Antigravity CLI) not found")
    assert a.sign_in_argv() is None
    with pytest.raises(FileNotFoundError):
        a.list_models()
    assert a.cached_models() == [info.ModelOption("", "Default (agy)")]


def test_agy_auth_never_raises(monkeypatch):
    def boom(pref):
        raise RuntimeError("kaput")

    monkeypatch.setattr(discover, "find_agy", boom)
    st = info.AgyInfo().auth_status()
    assert st.ok is False and st.detail == "Couldn't check: kaput"


def test_agy_not_signed_in(monkeypatch):
    _agy_found(monkeypatch)
    run, _ = _fake_run(stderr="please sign in", rc=1)
    monkeypatch.setattr(discover.subprocess, "run", run)
    a = info.AgyInfo()
    assert a.auth_status() == info.AuthStatus(False, "Not signed in: please sign in")
    with pytest.raises(RuntimeError):
        a.list_models()


def test_agy_explicit_path_overrides_pref(monkeypatch):
    seen = []
    monkeypatch.setattr(discover, "find_agy", lambda pref: seen.append(pref) or pref)
    assert info.AgyInfo(agy_path="D:/agy.exe").sign_in_argv() == ["D:/agy.exe"]
    assert seen == ["D:/agy.exe"]


def test_agy_live_model_id_and_tool_ref():
    a = info.AgyInfo()
    assert a.live_model_id("") is None
    assert a.live_model_id("gemini-3-pro") == "gemini-3-pro"
    assert a.tool_ref("bus", "list_agents") == "`lightfall_bus` / `list_agents`"
    assert info.ClaudeCodeInfo().tool_ref("bus", "list_agents") == "`mcp__bus__list_agents`"


def test_agy_sign_in_env_uses_private_home(tmp_path):
    env = info.AgyInfo().sign_in_env()  # spec_home patched to tmp_path by the fixture
    assert env == {"USERPROFILE": str(tmp_path), "HOME": str(tmp_path)}


def test_claude_cached_models_never_hits_network(monkeypatch):
    from lightfall.ui.preferences import claude_settings as cs

    monkeypatch.setattr(cs, "fetch_available_models",
                        lambda *a, **k: pytest.fail("network call"))
    monkeypatch.setattr(cs, "_MODELS_CACHE", {})
    monkeypatch.setattr(cs.ClaudeSettingsProvider, "get_base_url", staticmethod(lambda: "https://x"))
    models = info.ClaudeCodeInfo().cached_models()
    assert models[0] == info.ModelOption("", "Default (CLI)")
    assert [m.id for m in models[1:]] == ["claude-opus", "claude-sonnet", "claude-haiku"]


def test_claude_live_model_id_resolves_alias():
    assert info.ClaudeCodeInfo().live_model_id("claude-opus") == "opus"


def test_backend_info_dispatch(monkeypatch):
    assert isinstance(info.backend_info("agy"), info.AgyInfo)
    assert isinstance(info.backend_info("claude-code"), info.ClaudeCodeInfo)
    monkeypatch.setattr(info, "backend_kind", lambda: "agy")
    assert isinstance(info.backend_info(), info.AgyInfo)


def test_agy_model_ids_uses_the_models_cache(monkeypatch):
    run, calls = _fake_run(stdout=ROWS)
    monkeypatch.setattr(discover.subprocess, "run", run)
    assert info.agy_model_ids("C:/x/agy.exe") == frozenset({"gemini-3-pro", "gemini-3-flash"})
    info.AgyInfo(agy_path="C:/x/agy.exe")  # same cache as the settings page
    assert info.agy_model_ids("C:/x/agy.exe") == frozenset({"gemini-3-pro", "gemini-3-flash"})
    assert len(calls) == 1


def test_agy_model_ids_argv_list(monkeypatch):
    run, calls = _fake_run(stdout=ROWS)
    monkeypatch.setattr(discover.subprocess, "run", run)
    assert info.agy_model_ids(["py", "fake_agy.py"]) == frozenset({"gemini-3-pro", "gemini-3-flash"})
    assert calls[0][:2] == ["py", "fake_agy.py"]


def test_agy_model_ids_raises_when_not_signed_in(monkeypatch):
    run, _ = _fake_run(stderr="please sign in", rc=1)
    monkeypatch.setattr(discover.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="sign in"):
        info.agy_model_ids("C:/x/agy.exe")


def test_subagent_wording_per_backend():
    a, c = info.AgyInfo(), info.ClaudeCodeInfo()
    assert "invoke_subagent" in a.subagent_note and "invoke_subagent" in a.subagent_tag + a.subagent_note
    assert "Agent tool" not in a.subagent_note and "Task" not in a.subagent_note
    assert c.subagent_tag == "Agent-tool delegable" and "Agent tool" in c.subagent_note
