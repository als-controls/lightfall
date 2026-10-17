# tests/agents/agy/test_wiring.py
"""Backend selection (agent_backend pref) and agy session-home sweeping."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from unittest.mock import MagicMock

import pytest

from lightfall.agents.backend import SessionConfig
from lightfall.agents.backends.agy import home as agy_home
from lightfall.ui.panels.claude.tool_registry import ToolRegistry
from lightfall.ui.preferences import claude_settings


class _Prefs:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def prefs(monkeypatch):
    p = _Prefs({})
    monkeypatch.setattr(claude_settings.PreferencesManager, "get_instance", staticmethod(lambda: p))
    from lightfall.agents.backends import info

    info.clear_caches()
    yield p
    info.clear_caches()


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "agy-root"
    r.mkdir()
    monkeypatch.setattr(agy_home, "lightfall_agy_home", lambda: r)
    return r


@pytest.fixture
def sdk(monkeypatch):
    mock = MagicMock(side_effect=lambda **kw: MagicMock(name="sdk_client"))
    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", mock)
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.tool_registry.ToolRegistry._read_list_pref",
        lambda self, key: None,
    )
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.tool_registry.ToolRegistry._migrate_legacy_pref_if_needed",
        lambda self: None,
    )
    ToolRegistry.reset_instance()
    yield mock
    ToolRegistry.reset_instance()


@pytest.fixture
def agy_found(monkeypatch):
    monkeypatch.setattr("lightfall.agents.backends.agy.discover.find_agy",
                        lambda pref: r"C:\fake\agy.exe")


def _agent(qtbot, **kw):
    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    return QtClaudeAgent(target_window=target, require_approval=False, **kw)


# -- QtClaudeAgent construction ---------------------------------------------------

def test_agy_backend_skips_claude_client(qtbot, prefs, root, sdk, agy_found):
    from lightfall.agents.backends.agy.backend import AgyCliBackend

    prefs.values.update(agent_backend="agy", agy_gcp_project="proj-x", agy_gcp_location="loc-y")
    agent = _agent(qtbot, model="gemini-3-pro")
    try:
        sdk.assert_not_called()
        assert agent.client is None
        assert agent.options is None
        assert isinstance(agent._backend, AgyCliBackend)
        b = agent._backend
        assert b._agy == r"C:\fake\agy.exe"
        assert b._project == "proj-x"
        assert b._location == "loc-y"
        assert b._model == "gemini-3-pro"
        assert b._spec_name == (agent._spec.name if agent._spec else None)
    finally:
        agent.stop()


def test_agy_backend_missing_binary_raises_value_error(qtbot, prefs, root, sdk, monkeypatch):
    monkeypatch.setattr("lightfall.agents.backends.agy.discover.find_agy", lambda pref: None)
    prefs.values["agent_backend"] = "agy"
    with pytest.raises(ValueError, match="agy"):
        _agent(qtbot)
    sdk.assert_not_called()


def test_claude_default_unchanged(qtbot, prefs, sdk):
    agent = _agent(qtbot)
    try:
        sdk.assert_called_once_with(options=agent.options)
        assert agent.options is not None
        assert agent._backend is None
    finally:
        agent.stop()


def test_unknown_backend_value_falls_back_to_claude(qtbot, prefs, sdk):
    prefs.values["agent_backend"] = "bogus"
    assert claude_settings.ClaudeSettingsProvider.get_agent_backend() == "claude-code"
    agent = _agent(qtbot)
    try:
        sdk.assert_called_once()
        assert agent._backend is None
    finally:
        agent.stop()


def test_agy_reset_conversation_builds_fresh_backend(qtbot, prefs, root, sdk, agy_found):
    from lightfall.agents.backends.agy.backend import AgyCliBackend

    prefs.values["agent_backend"] = "agy"
    agent = _agent(qtbot, resume="conv-old")
    try:
        old = agent._backend
        assert agent._session_config.resume == "conv-old"
        agent.reset_conversation()
        assert isinstance(agent._backend, AgyCliBackend)
        assert agent._backend is not old
        assert agent._session_config.resume is None
        assert agent._backend.conversation_id is None
        assert agent._session_config.skills_dir == agent._session_plugin_dir
        assert agent._session_plugin_dir.exists()
        sdk.assert_not_called()
    finally:
        agent.stop()


def test_agy_set_model_unconnected_rebuilds_backend(qtbot, prefs, root, sdk, agy_found):
    prefs.values["agent_backend"] = "agy"
    agent = _agent(qtbot, resume="conv-1")
    try:
        agent.set_model("gemini-flash")
        assert agent._backend._model == "gemini-flash"
        assert agent._backend.conversation_id == "conv-1"
    finally:
        agent.stop()


def test_agy_ensure_connected_uses_agent_worker(qtbot, prefs, root, sdk, agy_found, monkeypatch):
    from lightfall.agents import worker as worker_mod
    from lightfall.claude import agent as agent_mod

    created = []

    def fake_worker(backend, permission_manager=None, parent=None):
        w = MagicMock()
        w.backend = backend
        w.isRunning.return_value = False  # never connects: _ensure_connected returns at once
        created.append(w)
        return w

    monkeypatch.setattr(worker_mod, "AgentWorker", fake_worker)
    claude_worker = MagicMock()
    monkeypatch.setattr(agent_mod, "PersistentClaudeWorker", claude_worker)
    prefs.values["agent_backend"] = "agy"
    agent = _agent(qtbot)
    assert agent._ensure_connected() is False
    assert created and created[0].backend is agent._backend
    claude_worker.assert_not_called()
    agent._worker = None


# -- session store / settings ------------------------------------------------------

def test_make_session_store_by_kind(root, tmp_path):
    from lightfall.agents.backends.agy.sessions import AgySessionStore
    from lightfall.agents.backends.claude_code.sessions import ClaudeSessionStore
    from lightfall.agents.backends.select import make_session_store

    agy = make_session_store("agy", str(tmp_path))
    assert isinstance(agy, AgySessionStore)
    assert agy._root == root / "lightfall" / ".gemini" / "antigravity-cli"
    assert isinstance(make_session_store("claude-code", str(tmp_path)), ClaudeSessionStore)
    assert isinstance(make_session_store("whatever", str(tmp_path)), ClaudeSessionStore)


def test_backend_kind_reads_pref(prefs):
    from lightfall.agents.backends.select import backend_kind

    assert backend_kind() == "claude-code"
    prefs.values["agent_backend"] = "agy"
    assert backend_kind() == "agy"


def test_agy_pref_defaults(prefs):
    p = claude_settings.ClaudeSettingsProvider
    assert p.get_agent_backend() == "claude-code"
    assert p.get_agy_path() == ""
    assert p.get_agy_project() == "lbl-gemini-enterprise-access"
    assert p.get_agy_location() == "global"
    assert p.get_agy_model() == ""


def test_is_configured_agy_requires_binary(prefs, monkeypatch):
    seen = []
    monkeypatch.setattr(claude_settings, "check_oauth_status",
                        lambda: pytest.fail("Claude OAuth must not be consulted for agy"))
    prefs.values.update(agent_backend="agy", agy_path=r"D:\x\agy.exe")

    def find(pref):
        seen.append(pref)
        return None

    monkeypatch.setattr("lightfall.agents.backends.agy.discover.find_agy", find)
    assert claude_settings.ClaudeSettingsProvider.is_configured() is False
    assert seen == [r"D:\x\agy.exe"]
    monkeypatch.setattr("lightfall.agents.backends.agy.discover.find_agy", lambda pref: "agy.exe")
    assert claude_settings.ClaudeSettingsProvider.is_configured() is True


def test_agy_auth_status_cached(prefs, root, monkeypatch):
    calls = []

    def models(agy, home):
        calls.append((agy, home))
        return [("a", "A"), ("b", "B"), ("c", "C")], "3 models"

    monkeypatch.setattr("lightfall.agents.backends.agy.discover.find_agy", lambda pref: "agy.exe")
    monkeypatch.setattr("lightfall.agents.backends.agy.discover.agy_models", models)
    prefs.values["agent_backend"] = "agy"
    p = claude_settings.ClaudeSettingsProvider
    assert p.get_auth_status() == (True, "Signed in (3 models)")
    assert p.get_auth_status() == (True, "Signed in (3 models)")
    assert calls == [("agy.exe", root / "lightfall")]
    # Expired cache entry -> re-checked.
    from lightfall.agents.backends import info

    for k, (ts, res) in list(info._AGY_CACHE.items()):
        info._AGY_CACHE[k] = (ts - 61, res)
    p.get_auth_status()
    assert len(calls) == 2


def test_agy_auth_status_without_binary(prefs, monkeypatch):
    monkeypatch.setattr("lightfall.agents.backends.agy.discover.find_agy", lambda pref: None)
    monkeypatch.setattr("lightfall.agents.backends.agy.discover.agy_models",
                        lambda *a: pytest.fail("must not run agy when it is missing"))
    prefs.values["agent_backend"] = "agy"
    ok, msg = claude_settings.ClaudeSettingsProvider.get_auth_status()
    assert ok is False and "agy" in msg


def test_session_tab_model_by_backend(prefs):
    from lightfall.ui.panels.claude.agent_session_tab import session_model

    prefs.values["claude_model"] = "claude-opus"
    assert session_model() == claude_settings.resolve_model_alias("claude-opus")
    prefs.values["agent_backend"] = "agy"
    assert session_model() is None
    prefs.values["agy_model"] = "gemini-3-pro"
    assert session_model() == "gemini-3-pro"


# -- stale session-home sweep -------------------------------------------------------

def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _session(spec, sid, pid: int | None):
    home = agy_home.prepare_session_home(spec, sid)
    if pid is not None:
        (home / agy_home.OWNER_FILE).write_text(str(pid), encoding="utf-8")
    return home


def test_pid_alive():
    assert agy_home.pid_alive(os.getpid()) is True
    assert agy_home.pid_alive(_dead_pid()) is False
    assert agy_home.pid_alive(0) is False
    assert agy_home.pid_alive(-5) is False


def test_sweep_removes_dead_keeps_live(root):
    store = agy_home.spec_home(None) / ".gemini" / "antigravity-cli"
    store.mkdir(parents=True, exist_ok=True)
    (store / "settings.json").write_text("{}", encoding="utf-8")
    dead = _session(None, "dead", _dead_pid())
    mine = _session(None, "mine", os.getpid())
    young = _session(None, "young", None)
    old = _session(None, "old", None)
    past = time.time() - 2 * 3600
    os.utime(old, (past, past))
    garbage = _session(None, "garbage", None)
    (garbage / agy_home.OWNER_FILE).write_text("not a pid", encoding="utf-8")
    os.utime(garbage, (past, past))

    removed = agy_home.sweep_stale_session_homes(None)

    assert sorted(p.name for p in removed) == ["dead", "garbage", "old"]
    assert not dead.exists() and not old.exists() and not garbage.exists()
    assert mine.exists() and young.exists()
    # The shared spec store survived the link removal.
    assert (store / "settings.json").read_text(encoding="utf-8") == "{}"


def test_sweep_keep_and_missing_dir(root):
    assert agy_home.sweep_stale_session_homes("nobody") == []
    keep = _session(None, "keep", _dead_pid())
    assert agy_home.sweep_stale_session_homes(None, keep=keep) == []
    assert keep.exists()


def test_connect_writes_owner_pid_and_sweeps(root, tmp_path):
    from lightfall.agents.backends.agy.backend import AgyCliBackend

    store = agy_home.spec_home("spec-a") / ".gemini" / "antigravity-cli"
    stale = _session("spec-a", "stale", _dead_pid())
    other_spec = _session("spec-b", "stale", _dead_pid())
    other_live = _session("spec-b", "live", os.getpid())
    main_young = _session(None, "young", None)
    work = tmp_path / "work"
    work.mkdir()
    cfg = SessionConfig(cwd=str(work), system_prompt="p", tool_servers=[], external_servers={},
                        allowed_tools=[], subagents=[], skills_dir=tmp_path)
    pm = MagicMock()
    b = AgyCliBackend(cfg, pm, False, agy="agy.exe", spec_name="spec-a",
                      project="p", location="l")

    async def run():
        await b.connect()
        try:
            home = b.session_home
            assert (home / agy_home.OWNER_FILE).read_text(encoding="utf-8").strip() == str(os.getpid())
            assert not stale.exists()
            assert not other_spec.exists()  # every spec is swept, not just spec-a
            assert other_live.exists() and main_young.exists()
            assert store.is_dir()
        finally:
            await b.close()

    asyncio.run(run())


def test_sweep_all_specs(root):
    a = _session("spec-a", "dead", _dead_pid())
    b = _session("spec-b", "dead", _dead_pid())
    keep_b = _session("spec-b", "mine", os.getpid())
    removed = agy_home.sweep_all_stale_session_homes()
    assert sorted((p.parent.parent.name, p.name) for p in removed) == [
        ("spec-a", "dead"), ("spec-b", "dead")]
    assert not a.exists() and not b.exists() and keep_b.exists()
    assert (agy_home.spec_home("spec-b") / ".gemini" / "antigravity-cli").is_dir()


# -- fix round 1 ----------------------------------------------------------------

def test_last_session_id_per_backend(prefs):
    p = claude_settings.ClaudeSettingsProvider
    p.set_last_session_id("claude-sess")
    assert prefs.values == {"claude_last_session_id": "claude-sess"}
    prefs.values["agent_backend"] = "agy"
    assert p.get_last_session_id() == ""
    p.set_last_session_id("agy-conv")
    assert prefs.values["agy_last_session_id"] == "agy-conv"
    assert prefs.values["claude_last_session_id"] == "claude-sess"
    prefs.values["agent_backend"] = "claude-code"
    assert p.get_last_session_id() == "claude-sess"
    p.set_last_session_id("")
    assert prefs.values["agy_last_session_id"] == "agy-conv"


def test_pick_model_noop_on_agy(qtbot, prefs, monkeypatch):
    from lightfall.ui.panels.claude_panel import ClaudePanel

    monkeypatch.setattr(ClaudePanel, "_setup_ui", lambda self: None)
    panel = ClaudePanel()
    qtbot.addWidget(panel)
    widget = MagicMock()
    panel._claude_widget = widget
    prefs.values["agent_backend"] = "agy"
    panel._on_pick_model("claude-opus")
    widget.agent.set_model.assert_not_called()
    assert "claude_model" not in prefs.values


def test_agy_reset_rebuild_failure_drops_old_backend(qtbot, prefs, root, sdk, agy_found,
                                                     monkeypatch):
    from lightfall.agents import worker as worker_mod
    from lightfall.agents.backends.agy.backend import AgyCliBackend

    prefs.values["agent_backend"] = "agy"
    agent = _agent(qtbot, resume="conv-old")
    errors = []
    agent.error_occurred.connect(errors.append)
    real = agent._make_agy_backend

    def boom():
        raise ValueError("agy (Antigravity CLI) not found")

    monkeypatch.setattr(agent, "_make_agy_backend", boom)
    agent.reset_conversation()
    assert agent._backend is None
    assert agent._session_config.resume is None
    assert errors and "agy" in errors[-1]

    # Next query: still failing -> no worker, error surfaced, nothing resumed.
    assert agent._ensure_connected() is False
    assert agent._worker is None or agent._backend is None

    # Once agy is back, the rebuilt backend starts a NEW conversation.
    created = []

    def fake_worker(backend, permission_manager=None, parent=None):
        w = MagicMock()
        w.isRunning.return_value = False
        created.append(backend)
        return w

    monkeypatch.setattr(worker_mod, "AgentWorker", fake_worker)
    monkeypatch.setattr(agent, "_make_agy_backend", real)
    agent._ensure_connected()
    assert isinstance(created[0], AgyCliBackend)
    assert created[0].conversation_id is None
    sdk.assert_not_called()
    agent._worker = None


# -- last-session id belongs to the main assistant only ---------------------------

def _persisted(monkeypatch):
    from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

    calls = []
    monkeypatch.setattr(ClaudeSettingsProvider, "set_last_session_id",
                        staticmethod(lambda sid: calls.append(sid)))
    return calls


def test_non_main_agent_leaves_last_session_id_alone(qtbot, prefs, root, sdk, agy_found, monkeypatch):
    from types import SimpleNamespace

    prefs.values.update(agent_backend="agy")
    agent = _agent(qtbot)
    try:
        agent._spec = SimpleNamespace(name="scanner", skills=[])
        calls = _persisted(monkeypatch)
        agent._store_session_id("other-conv")
        assert agent._current_session_id == "other-conv"
        agent.reset_conversation()
        assert calls == []
    finally:
        agent.stop()


def test_main_agent_still_stores_and_clears_last_session_id(qtbot, prefs, root, sdk, agy_found, monkeypatch):
    prefs.values.update(agent_backend="agy")
    agent = _agent(qtbot)
    try:
        agent._spec = None
        calls = _persisted(monkeypatch)
        agent._store_session_id("main-conv")
        assert calls == ["main-conv"]
        agent.reset_conversation()
        assert calls == ["main-conv", ""]
    finally:
        agent.stop()
