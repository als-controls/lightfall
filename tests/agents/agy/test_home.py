# tests/agents/agy/test_home.py
from __future__ import annotations

import json
import os
import sys
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
            "allow": ["mcp(lightfall_qt/*)", "mcp(lightfall_bus/*)",
                      "read_file(*)", "write_file(*)", "command(*)", "read_url(*)"],
            "deny": ["execute_url(*)"],
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
    assert s["permissions"]["allow"] == [
        "mcp(lightfall_qt/*)", "read_file(*)", "write_file(*)", "command(*)", "read_url(*)",
        "mcp(lightfall_bus/*)", "mcp(lightfall_ask/*)"]
    assert s["permissions"]["deny"] == ["execute_url(*)"]
    assert s["gcp"] == {"project": "p3", "location": "eu"}
    assert [x.name for x in p.parent.iterdir()] == ["settings.json"]  # no temp leftovers


def test_spec_settings_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "spec" / ".gemini" / "antigravity-cli" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    agy_home.write_spec_settings(tmp_path / "spec", workspace="w", server_keys=["qt"])
    assert _load(p)["permissions"]["allow"] == [
        "mcp(lightfall_qt/*)", "read_file(*)", "write_file(*)", "command(*)", "read_url(*)"]


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
        "tools: [view_file, list_dir, grep_search, find_by_name, write_to_file, replace_file_content, "
        "multi_replace_file_content, edit_file, run_command, read_url_content, search_web, "
        "invoke_subagent]\n"
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


@pytest.mark.skipif(sys.platform != "win32", reason="needs _winapi (Windows only)")
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


# ---- subagents -------------------------------------------------------------

_SUB_TOOLS = ("tools: [view_file, list_dir, grep_search, find_by_name, write_to_file, "
              "replace_file_content, multi_replace_file_content, edit_file, run_command, "
              "read_url_content, search_web]\n")


def _sub(name, description="d", prompt="p", model=None, **kw):
    from lightfall.agents.backend import SubagentSpec

    return SubagentSpec(name=name, description=description, prompt=prompt, model=model, **kw)


def _agents(session):
    return sorted(p.name for p in (_plugin(session) / "agents").iterdir())


def _capture(level="DEBUG"):
    from lightfall.utils.logging import logger

    msgs: list[str] = []
    sink = logger.add(lambda m: msgs.append(str(m)), level=level)
    return msgs, lambda: logger.remove(sink)


def test_subagent_files_exact(tmp_path):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg(
        subagents=[_sub("echoer", "Echoes a word.", "You echo.", model="gemini-3-flash",
                        effort="high", memory=True),
                   _sub("saxs", "SAXS reduction: q-space", "You reduce SAXS.\nCarefully.")],
        model_ids=frozenset({"gemini-3-flash", "gemini-3-pro"})))
    assert _agents(session) == ["echoer.md", "operator.md", "saxs.md"]
    agents = _plugin(session) / "agents"
    assert (agents / "echoer.md").read_text(encoding="utf-8") == (
        "---\n"
        'name: "echoer"\n'
        'description: "Echoes a word."\n'
        "mainAgent: false\n"
        "subagent: true\n"
        "excludeDefaultComponents: true\n"
        "inheritCustomizations: true\n"
        "inheritMcp: true\n"
        + _SUB_TOOLS +
        "model: gemini-3-flash\n"
        "---\n"
        "\n"
        "You echo.\n"
    )
    assert (agents / "saxs.md").read_text(encoding="utf-8") == (
        "---\n"
        'name: "saxs"\n'
        'description: "SAXS reduction: q-space"\n'
        "mainAgent: false\n"
        "subagent: true\n"
        "excludeDefaultComponents: true\n"
        "inheritCustomizations: true\n"
        "inheritMcp: true\n"
        + _SUB_TOOLS +
        "model: inherit\n"
        "---\n"
        "\n"
        "You reduce SAXS.\nCarefully.\n"
    )


def test_operator_offers_invoke_subagent_but_subagents_do_not(tmp_path):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg(subagents=[_sub("echoer")]))
    agents = _plugin(session) / "agents"

    def tools_line(f):
        return next(ln for ln in (agents / f).read_text(encoding="utf-8").splitlines()
                    if ln.startswith("tools:"))

    assert "invoke_subagent" in tools_line("operator.md")
    assert "invoke_subagent" not in tools_line("echoer.md")


@pytest.mark.parametrize("claude_model", ["sonnet", "haiku", "opus", "claude-opus-4-1"])
def test_claude_model_becomes_inherit(tmp_path, claude_model):
    session = tmp_path / "s"
    msgs, done = _capture()
    try:
        agy_home.write_session_config(session, _cfg(
            subagents=[_sub("echoer", model=claude_model)],
            model_ids=frozenset({"gemini-3-flash"})))
    finally:
        done()
    text = (_plugin(session) / "agents" / "echoer.md").read_text(encoding="utf-8")
    assert "model: inherit\n" in text and claude_model not in text
    assert any(claude_model in m and "DEBUG" in m for m in msgs)


@pytest.mark.parametrize("alias", ["inherit", "flash", "flash_lite", "pro"])
def test_agy_model_aliases_kept(tmp_path, alias):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg(subagents=[_sub("echoer", model=alias)]))
    text = (_plugin(session) / "agents" / "echoer.md").read_text(encoding="utf-8")
    assert f"model: {alias}\n" in text


@pytest.mark.parametrize("desc", ["", "   "])
def test_empty_description_falls_back_to_name(tmp_path, desc):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg(subagents=[_sub("echoer", desc)]))
    text = (_plugin(session) / "agents" / "echoer.md").read_text(encoding="utf-8")
    assert 'description: "echoer"\n' in text


def test_multiline_description_stays_one_yaml_line(tmp_path):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg(subagents=[_sub("echoer", 'say "hi"\nthen go')]))
    text = (_plugin(session) / "agents" / "echoer.md").read_text(encoding="utf-8")
    assert 'description: "say \\"hi\\"\\nthen go"\n' in text


def test_invalid_and_duplicate_subagent_names_skipped(tmp_path):
    session = tmp_path / "s"
    msgs, done = _capture("WARNING")
    try:
        agy_home.write_session_config(session, _cfg(subagents=[
            _sub("good", prompt="first"), _sub("a b"), _sub("../evil"), _sub(""), _sub(".."),
            _sub("good", prompt="second"), _sub("GOOD", prompt="third")]))
    finally:
        done()
    assert _agents(session) == ["good.md", "operator.md"]
    text = (_plugin(session) / "agents" / "good.md").read_text(encoding="utf-8")
    assert text.endswith("\nfirst\n")
    assert sum("subagent" in m for m in msgs) == 6
    assert not (_plugin(session) / "evil.md").exists()


@pytest.mark.parametrize("name", ["operator", "Operator"])
def test_subagent_named_operator_rejected(tmp_path, name):
    session = tmp_path / "s"
    msgs, done = _capture("WARNING")
    try:
        agy_home.write_session_config(session, _cfg(subagents=[_sub(name, prompt="EVIL")]))
    finally:
        done()
    assert _agents(session) == ["operator.md"]
    op = (_plugin(session) / "agents" / "operator.md").read_text(encoding="utf-8")
    assert "mainAgent: true" in op and "EVIL" not in op
    assert any("operator" in m for m in msgs)


def test_stale_subagent_files_removed(tmp_path):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg(subagents=[_sub("old"), _sub("keep")]))
    (_plugin(session) / "agents" / "leftover.md").write_text("x", encoding="utf-8")
    agy_home.write_session_config(session, _cfg(subagents=[_sub("keep"), _sub("new")]))
    assert _agents(session) == ["keep.md", "new.md", "operator.md"]
    agy_home.write_session_config(session, _cfg())
    assert _agents(session) == ["operator.md"]


def test_subagent_body_carries_tool_directory(tmp_path):
    session = tmp_path / "s"
    directory = "## Calling Lightfall tools\n\n### `lightfall_probe`\n- `echo` — Echo\n"
    agy_home.write_session_config(session, _cfg(subagents=[_sub("echoer", prompt="You echo.")],
                                                tool_directory=directory))
    text = (_plugin(session) / "agents" / "echoer.md").read_text(encoding="utf-8")
    assert text.endswith("---\n\nYou echo.\n\n" + directory)


def test_subagent_body_without_tool_directory_is_just_the_prompt(tmp_path):
    session = tmp_path / "s"
    agy_home.write_session_config(session, _cfg(subagents=[_sub("echoer", prompt="You echo.")]))
    text = (_plugin(session) / "agents" / "echoer.md").read_text(encoding="utf-8")
    assert text.endswith("---\n\nYou echo.\n")
