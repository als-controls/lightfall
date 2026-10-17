# tests/agents/agy/test_home.py
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lightfall.agents.backends.agy import home as agy_home


def _cfg(tmp_path, **kw):
    base = {
        "home": tmp_path / "home",
        "workspace": str(tmp_path / "ws"),
        "servers": ["qt", "bus"],
        "external_servers": {"ext": {"type": "stdio", "command": "x", "args": ["1"]}},
        "system_prompt": "You are the operator.",
        "hook_command": "python -I hook.py",
    }
    base.update(kw)
    return agy_home.AgyHomeConfig(**base)


def _plugin(cfg):
    return cfg.home / ".gemini" / "config" / "plugins" / "lightfall"


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def test_writes_settings_exact(tmp_path):
    cfg = _cfg(tmp_path)
    agy_home.write_agy_home(cfg)
    s = _load(cfg.home / ".gemini" / "antigravity-cli" / "settings.json")
    assert s == {
        "gcp": {"project": "lbl-gemini-enterprise-access", "location": "global"},
        "trustedWorkspaces": [os.path.normpath(cfg.workspace)],
        "permissions": {
            # agy matches the server part literally: one allow entry per server key.
            "allow": ["mcp(lightfall_qt/*)", "mcp(lightfall_bus/*)", "mcp(lightfall_ext/*)"],
            "deny": ["command(*)", "write_file(*)", "read_url(*)", "execute_url(*)"],
        },
    }


def test_settings_project_location_overridable(tmp_path):
    cfg = _cfg(tmp_path, project="p", location="us")
    agy_home.write_agy_home(cfg)
    s = _load(cfg.home / ".gemini" / "antigravity-cli" / "settings.json")
    assert s["gcp"] == {"project": "p", "location": "us"}


def test_plugin_json_and_enablement(tmp_path):
    cfg = _cfg(tmp_path)
    plugin = agy_home.write_agy_home(cfg)
    assert plugin == _plugin(cfg)
    assert _load(plugin / "plugin.json") == {
        "name": "lightfall",
        "description": "Lightfall session tools, rules and skills.",
    }
    # the record `agy plugin enable lightfall` writes (verified live)
    assert _load(cfg.home / ".gemini" / "config" / "config.json") == {
        "plugins": {"lightfall": {"enabled": True}}
    }


def test_mcp_config_has_one_http_entry_per_server_plus_externals(tmp_path):
    cfg = _cfg(tmp_path)
    agy_home.write_agy_home(cfg, port=4242, token="tok-abc")
    m = _load(_plugin(cfg) / "mcp_config.json")["mcpServers"]
    assert set(m) == {"qt", "bus", "ext"}
    assert m["qt"] == {
        "type": "http",
        "url": "http://127.0.0.1:4242/mcp/qt/",
        "headers": {"Authorization": "Bearer tok-abc"},
    }
    assert m["bus"]["url"] == "http://127.0.0.1:4242/mcp/bus/"
    assert m["ext"] == {"type": "stdio", "command": "x", "args": ["1"]}


def test_mcp_config_env_placeholders_without_port_token(tmp_path):
    cfg = _cfg(tmp_path)
    agy_home.write_agy_home(cfg)
    m = _load(_plugin(cfg) / "mcp_config.json")["mcpServers"]
    assert m["qt"]["url"] == "http://127.0.0.1:${LF_AGY_PORT}/mcp/qt/"
    assert m["qt"]["headers"] == {"Authorization": "Bearer ${LF_AGY_TOKEN}"}


def test_hooks_json_command_and_timeout(tmp_path):
    cfg = _cfg(tmp_path)
    agy_home.write_agy_home(cfg)
    assert _load(_plugin(cfg) / "hooks.json") == {
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
    cfg = _cfg(tmp_path)
    agy_home.write_agy_home(cfg)
    plugin = _plugin(cfg)
    assert (plugin / "rules" / "AGENTS.md").read_text(encoding="utf-8") == "You are the operator."
    text = (plugin / "agents" / "operator.md").read_text(encoding="utf-8")
    assert text == (
        "---\n"
        "name: operator\n"
        "description: Lightfall beamline control assistant\n"
        "mainAgent: true\n"
        "subagent: false\n"
        "excludeDefaultComponents: true\n"
        "inheritCustomizations: true\n"
        "inheritMcp: true\n"
        "tools: []\n"
        "---\n"
        "\n"
        "You are the operator.\n"
    )


def test_rules_and_skills_copied_and_replaced(tmp_path):
    src1 = tmp_path / "skills_src1"
    (src1 / "foo").mkdir(parents=True)
    (src1 / "foo" / "SKILL.md").write_text("foo skill", encoding="utf-8")
    cfg = _cfg(tmp_path, skills_src=src1)
    agy_home.write_agy_home(cfg)
    assert (_plugin(cfg) / "skills" / "foo" / "SKILL.md").read_text(encoding="utf-8") == "foo skill"

    src2 = tmp_path / "skills_src2"
    (src2 / "bar").mkdir(parents=True)
    (src2 / "bar" / "SKILL.md").write_text("bar skill", encoding="utf-8")
    cfg.skills_src = src2
    agy_home.write_agy_home(cfg)
    assert (_plugin(cfg) / "skills" / "bar" / "SKILL.md").is_file()
    assert not (_plugin(cfg) / "skills" / "foo").exists()

    cfg.skills_src = None
    agy_home.write_agy_home(cfg)
    assert (_plugin(cfg) / "skills").is_dir()
    assert list((_plugin(cfg) / "skills").iterdir()) == []


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_rewrite_is_idempotent(tmp_path):
    src = tmp_path / "skills_src"
    (src / "foo").mkdir(parents=True)
    (src / "foo" / "SKILL.md").write_text("foo", encoding="utf-8")
    cfg = _cfg(tmp_path, skills_src=src)
    agy_home.write_agy_home(cfg)
    first = _snapshot(cfg.home)
    agy_home.write_agy_home(cfg)
    assert _snapshot(cfg.home) == first


def test_no_secret_in_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LF_AGY_TOKEN", "super-secret-token-123")
    monkeypatch.setenv("LF_AGY_PORT", "55555")
    cfg = _cfg(tmp_path)
    agy_home.write_agy_home(cfg)
    for name, data in _snapshot(cfg.home).items():
        assert b"super-secret-token-123" not in data, name
        assert b"55555" not in data, name


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


@pytest.mark.parametrize(
    "spec,session,tail",
    [
        (None, None, ["lightfall"]),
        ("Scanner", None, ["Scanner"]),
        ("Scanner", "abc-123", ["Scanner", "abc-123"]),
        (None, "abc-123", ["lightfall", "abc-123"]),
    ],
)
def test_agy_home_for(tmp_path, monkeypatch, spec, session, tail):
    monkeypatch.setattr(agy_home, "lightfall_agy_home", lambda: tmp_path / "root")
    p = agy_home.agy_home_for(spec) if session is None else agy_home.agy_home_for(spec, session)
    assert p == tmp_path.joinpath("root", *tail)
    assert p.is_dir()
