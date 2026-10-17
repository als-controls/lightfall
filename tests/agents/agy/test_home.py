# tests/agents/agy/test_home.py
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lightfall.agents.backends.agy import home as agy_home

TOKEN = "super-secret-token-123"


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "root"
    r.mkdir()
    monkeypatch.setattr(agy_home, "lightfall_agy_home", lambda: r)
    return r


def _cfg(**kw):
    base = {
        "servers": ["qt", "bus"],
        "external_servers": {"ext": {"type": "stdio", "command": "x", "args": ["1"]}},
        "system_prompt": "You are the operator.",
        "hook_command": "python -I hook.py",
        "port": 4242,
        "token": TOKEN,
    }
    base.update(kw)
    return agy_home.AgyHomeConfig(**base)


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _plugin(session):
    return Path(session) / ".gemini" / "config" / "plugins" / "lightfall"


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _skip_if_no_link(tmp_path):
    try:
        os.symlink(tmp_path, tmp_path / "probe-link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink privilege missing on this machine")
    (tmp_path / "probe-link").unlink()


# ---- paths -----------------------------------------------------------------


def test_pure_paths_have_no_side_effects(root):
    assert agy_home.spec_home(None) == root / "lightfall"
    assert agy_home.spec_home("Scanner") == root / "Scanner"
    assert agy_home.session_home_path("Scanner", "abc") == root / "Scanner" / "sessions" / "abc"
    assert list(root.iterdir()) == []


def test_lightfall_agy_home_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(agy_home.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    p = agy_home.lightfall_agy_home()
    assert p == tmp_path / "AppData" / "ncs" / "agy-home"
    assert p.is_dir()


def test_lightfall_agy_home_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(agy_home.sys, "platform", "linux")
    monkeypatch.setattr(agy_home.Path, "home", classmethod(lambda cls: tmp_path))
    p = agy_home.lightfall_agy_home()
    assert p == tmp_path / ".config" / "ncs" / "agy-home"
    assert p.is_dir()


# ---- session home link -----------------------------------------------------


def test_prepare_session_home_links_spec_store(root):
    if os.name != "nt":
        _skip_if_no_link(root)
    session = agy_home.prepare_session_home("Scanner", "s1")
    store = root / "Scanner" / ".gemini" / "antigravity-cli"
    link = session / ".gemini" / "antigravity-cli"
    assert session == agy_home.session_home_path("Scanner", "s1")
    assert (session / ".gemini" / "config").is_dir()
    assert store.is_dir() and not store.is_symlink()
    assert os.path.samefile(link, store)
    # a file written through the session path lands in the spec store
    (link / "conversation_summaries.db").write_bytes(b"x")
    assert (store / "conversation_summaries.db").read_bytes() == b"x"
    # idempotent: link kept
    assert agy_home.prepare_session_home("Scanner", "s1") == session
    assert os.path.samefile(link, store)


def test_remove_session_home_keeps_store(root):
    if os.name != "nt":
        _skip_if_no_link(root)
    session = agy_home.prepare_session_home(None, "s1")
    store = root / "lightfall" / ".gemini" / "antigravity-cli"
    (store / "sentinel").write_text("keep", encoding="utf-8")
    agy_home.write_session_config(session, _cfg())
    agy_home.remove_session_home(session)
    assert not session.exists()
    assert (store / "sentinel").read_text(encoding="utf-8") == "keep"
    agy_home.remove_session_home(session)  # idempotent


def test_remove_session_home_when_link_already_deleted(root):
    session = agy_home.prepare_session_home(None, "s1")
    store = root / "lightfall" / ".gemini" / "antigravity-cli"
    (store / "sentinel").write_text("keep", encoding="utf-8")
    link = session / ".gemini" / "antigravity-cli"
    agy_home._remove_link(link)
    assert not os.path.lexists(link)
    agy_home.remove_session_home(session)
    assert not session.exists()
    assert (store / "sentinel").exists()


def test_remove_session_home_missing_path(root):
    agy_home.remove_session_home(root / "nope" / "sessions" / "x")


def test_prepare_replaces_wrong_link(root):
    if os.name != "nt":
        _skip_if_no_link(root)
    session = agy_home.prepare_session_home(None, "s1")
    link = session / ".gemini" / "antigravity-cli"
    other = root / "other"
    other.mkdir()
    agy_home._remove_link(link)
    agy_home._make_dir_link(link, other)
    agy_home.prepare_session_home(None, "s1")
    assert os.path.samefile(link, root / "lightfall" / ".gemini" / "antigravity-cli")
    assert other.is_dir()


def test_posix_symlink_path(root, monkeypatch):
    _skip_if_no_link(root)
    monkeypatch.setattr(agy_home.sys, "platform", "linux")
    session = agy_home.prepare_session_home("P", "s1")
    link = session / ".gemini" / "antigravity-cli"
    assert link.is_symlink()
    (root / "P" / ".gemini" / "antigravity-cli" / "s").write_text("1", encoding="utf-8")
    agy_home.remove_session_home(session)
    assert not session.exists()
    assert (root / "P" / ".gemini" / "antigravity-cli" / "s").exists()


# ---- spec settings ---------------------------------------------------------


def test_spec_settings_exact(tmp_path):
    ws = str(tmp_path / "ws")
    agy_home.write_spec_settings(tmp_path / "spec", workspace=ws, server_keys=["qt", "bus"])
    s = _load(tmp_path / "spec" / ".gemini" / "antigravity-cli" / "settings.json")
    assert s == {
        "gcp": {"project": "lbl-gemini-enterprise-access", "location": "global"},
        "trustedWorkspaces": [os.path.normpath(ws)],
        "permissions": {
            "allow": ["mcp(lightfall_qt/*)", "mcp(lightfall_bus/*)"],
            "deny": ["command(*)", "write_file(*)", "read_url(*)", "execute_url(*)"],
        },
    }


def test_spec_settings_merge_is_union(tmp_path):
    spec = tmp_path / "spec"
    agy_home.write_spec_settings(spec, workspace=str(tmp_path / "a"), server_keys=["qt"])
    p = agy_home.write_spec_settings(
        spec, workspace=str(tmp_path / "b"), server_keys=["qt", "bus"], project="p2", location="us"
    )
    # tamper with deny to prove it is reset
    s = _load(p)
    s["permissions"]["deny"] = []
    p.write_text(json.dumps(s), encoding="utf-8")
    agy_home.write_spec_settings(
        spec, workspace=str(tmp_path / "a"), server_keys=["ask"], project="p3", location="eu"
    )
    s = _load(p)
    assert s["trustedWorkspaces"] == [os.path.normpath(tmp_path / "a"), os.path.normpath(tmp_path / "b")]
    assert s["permissions"]["allow"] == ["mcp(lightfall_qt/*)", "mcp(lightfall_bus/*)", "mcp(lightfall_ask/*)"]
    assert s["permissions"]["deny"] == ["command(*)", "write_file(*)", "read_url(*)", "execute_url(*)"]
    assert s["gcp"] == {"project": "p3", "location": "eu"}
    assert [x.name for x in p.parent.iterdir()] == ["settings.json"]  # no temp leftovers


def test_spec_settings_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "spec" / ".gemini" / "antigravity-cli" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    agy_home.write_spec_settings(tmp_path / "spec", workspace="w", server_keys=["qt"])
    assert _load(p)["permissions"]["allow"] == ["mcp(lightfall_qt/*)"]


# ---- session config --------------------------------------------------------


def test_plugin_json_and_enablement(tmp_path):
    session = tmp_path / "s"
    plugin = agy_home.write_session_config(session, _cfg())
    assert plugin == _plugin(session)
    assert _load(plugin / "plugin.json") == {
        "name": "lightfall",
        "description": "Lightfall session tools, rules and skills.",
    }
    assert _load(session / ".gemini" / "config" / "config.json") == {
        "plugins": {"lightfall": {"enabled": True}}
    }


def test_session_config_writes_only_config_dir(tmp_path):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg())
    assert [p.name for p in (session / ".gemini").iterdir()] == ["config"]


def test_mcp_config_has_one_http_entry_per_server_plus_externals(tmp_path):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg())
    m = _load(_plugin(session) / "mcp_config.json")["mcpServers"]
    assert set(m) == {"qt", "bus", "ext"}
    assert m["qt"] == {
        "type": "http",
        "url": "http://127.0.0.1:4242/mcp/qt/",
        "headers": {"Authorization": f"Bearer {TOKEN}"},
    }
    assert m["bus"]["url"] == "http://127.0.0.1:4242/mcp/bus/"
    assert m["ext"] == {"type": "stdio", "command": "x", "args": ["1"]}


def test_hooks_json_command_and_timeout(tmp_path):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg())
    assert _load(_plugin(session) / "hooks.json") == {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [{"type": "command", "command": "python -I hook.py", "timeout": 290}],
                }
            ]
        }
    }


def test_rules_and_operator_agent(tmp_path):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg())
    plugin = _plugin(session)
    assert (plugin / "rules" / "AGENTS.md").read_text(encoding="utf-8") == "You are the operator."
    assert (plugin / "agents" / "operator.md").read_text(encoding="utf-8") == (
        "---\n"
        "name: operator\n"
        "description: Lightfall beamline control assistant\n"
        "mainAgent: true\n"
        "subagent: false\n"
        "excludeDefaultComponents: true\n"
        "inheritCustomizations: true\n"
        "inheritMcp: true\n"
        "tools: [view_file]\n"
        "---\n"
        "\n"
        "You are the operator.\n"
    )


def test_rules_and_skills_copied_and_replaced(tmp_path):
    session = tmp_path / "s"
    src1 = tmp_path / "skills_src1"
    (src1 / "foo").mkdir(parents=True)
    (src1 / "foo" / "SKILL.md").write_text("foo skill", encoding="utf-8")
    agy_home.write_session_config(session, _cfg(skills_src=src1))
    assert (_plugin(session) / "skills" / "foo" / "SKILL.md").read_text(encoding="utf-8") == "foo skill"

    src2 = tmp_path / "skills_src2"
    (src2 / "bar").mkdir(parents=True)
    (src2 / "bar" / "SKILL.md").write_text("bar skill", encoding="utf-8")
    agy_home.write_session_config(session, _cfg(skills_src=src2))
    assert (_plugin(session) / "skills" / "bar" / "SKILL.md").is_file()
    assert not (_plugin(session) / "skills" / "foo").exists()

    agy_home.write_session_config(session, _cfg())
    assert list((_plugin(session) / "skills").iterdir()) == []


def test_rewrite_is_idempotent(tmp_path):
    session = tmp_path / "s"
    src = tmp_path / "skills_src"
    (src / "foo").mkdir(parents=True)
    (src / "foo" / "SKILL.md").write_text("foo", encoding="utf-8")
    agy_home.write_session_config(session, _cfg(skills_src=src))
    first = _snapshot(session)
    agy_home.write_session_config(session, _cfg(skills_src=src))
    assert _snapshot(session) == first


def test_token_only_in_session_mcp_config_and_gone_after_remove(root):
    ws = str(root.parent / "ws")
    agy_home.write_spec_settings(agy_home.spec_home(None), workspace=ws, server_keys=["qt", "bus"])
    session = agy_home.prepare_session_home(None, "s1")
    agy_home.write_session_config(session, _cfg())
    hits = [n for n, d in _snapshot(root).items() if TOKEN.encode() in d]
    assert len(hits) == 1 and hits[0].endswith("mcp_config.json")
    assert "sessions" in hits[0]
    agy_home.remove_session_home(session)
    assert [n for n, d in _snapshot(root).items() if TOKEN.encode() in d] == []


# ---- key validation --------------------------------------------------------


def test_external_key_collision_rejected(tmp_path):
    with pytest.raises(ValueError, match="collides"):
        agy_home.write_session_config(tmp_path / "s", _cfg(external_servers={"qt": {"command": "x"}}))


@pytest.mark.parametrize("bad", ["a b", "a/b", "", "a*", "ü"])
def test_invalid_keys_rejected(tmp_path, bad):
    with pytest.raises(ValueError, match="invalid"):
        agy_home.write_session_config(tmp_path / "s", _cfg(servers=[bad]))
    with pytest.raises(ValueError, match="invalid"):
        agy_home.write_session_config(tmp_path / "s", _cfg(external_servers={bad: {}}))
    with pytest.raises(ValueError, match="invalid"):
        agy_home.write_spec_settings(tmp_path / "spec", workspace="w", server_keys=[bad])


@pytest.mark.parametrize("bad", ["..", ".", "a/b", "a\b", "x y", "../evil", "é"])
def test_spec_home_rejects_unsafe_names(root, bad):
    with pytest.raises(ValueError):
        agy_home.spec_home(bad)
    with pytest.raises(ValueError):
        agy_home.session_home_path(bad, "abc")


@pytest.mark.parametrize("ok", ["Scanner", "a-b_c.d", "lightfall", "v1.2"])
def test_spec_home_accepts_safe_names(root, ok):
    assert agy_home.spec_home(ok) == root / ok


def test_mklink_fallback_hides_console_window(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(agy_home.sys, "platform", "win32")
    import _winapi

    def _boom(*a):
        raise OSError("no junction")

    monkeypatch.setattr(_winapi, "CreateJunction", _boom, raising=False)
    monkeypatch.setattr(agy_home.subprocess, "run", lambda *a, **kw: calls.append(kw))
    agy_home._make_dir_link(tmp_path / "l", tmp_path / "t")
    assert calls[0]["creationflags"] == 0x08000000


def test_remove_session_home_warns_when_unlink_fails(root, monkeypatch):
    from lightfall.utils.logging import logger

    msgs = []
    sink = logger.add(lambda m: msgs.append(str(m)), level="WARNING")
    session = root / "lightfall" / "sessions" / "s1"
    (session / ".gemini" / "config").mkdir(parents=True)

    def _fail(link):
        raise OSError("busy")

    monkeypatch.setattr(agy_home, "_remove_link", _fail)
    try:
        agy_home.remove_session_home(session)
    finally:
        logger.remove(sink)
    assert session.exists()  # never rmtree'd
    assert any("Could not unlink" in m for m in msgs)
