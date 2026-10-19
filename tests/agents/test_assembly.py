from pathlib import Path

from lightfall.agents.assembly import (
    agent_cwd,
    assemble_spec_options,
    identity_preamble,
    subagent_definitions,
)
from lightfall.agents.registry import AgentSpecRegistry
from lightfall.agents.spec import AgentSpec


def _spec(name, **kw):
    defaults = dict(description=f"d-{name}", prompt="p {{user}}", scope="core",
                    source_path=Path(f"{name}.md"))
    defaults.update(kw)
    return AgentSpec(name=name, **defaults)


def test_agent_cwd_main_vs_named(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert agent_cwd(_spec("lightfall")) == str(tmp_path / "lightfall")
    assert agent_cwd(_spec("saxs")) == str(tmp_path / "lightfall" / "agents" / "saxs")
    assert (tmp_path / "lightfall" / "agents" / "saxs").is_dir()


def test_subagent_definitions_exclude_self_and_non_subagents(monkeypatch):
    AgentSpecRegistry.reset_instance()
    reg = AgentSpecRegistry.get_instance()
    specs = [_spec("lightfall"), _spec("saxs"), _spec("observer", subagent=False)]
    monkeypatch.setattr(reg, "enabled_specs", lambda: specs)
    monkeypatch.setattr("lightfall.agents.assembly.template_variables",
                        lambda: {"user": "ron", "beamline": "", "endstation": ""})
    defs = subagent_definitions(specs[0], reg)
    assert set(defs) == {"saxs"}
    assert defs["saxs"].prompt == "p ron"
    AgentSpecRegistry.reset_instance()


def test_subagent_definitions_skips_spec_with_unknown_template_var(monkeypatch):
    AgentSpecRegistry.reset_instance()
    reg = AgentSpecRegistry.get_instance()
    specs = [
        _spec("lightfall"),
        _spec("saxs"),
        _spec("bad", prompt="p {{typo}}"),
    ]
    monkeypatch.setattr(reg, "enabled_specs", lambda: specs)
    monkeypatch.setattr("lightfall.agents.assembly.template_variables",
                        lambda: {"user": "ron", "beamline": "", "endstation": ""})
    defs = subagent_definitions(specs[0], reg)
    assert set(defs) == {"saxs"}
    AgentSpecRegistry.reset_instance()


def test_builtin_lightfall_and_observer_load():
    from lightfall.agents import builtin_agents_dir  # exported from lightfall.agents.__init__
    from lightfall.agents.spec import parse_agent_file
    names = {parse_agent_file(p, "core").name for p in builtin_agents_dir().glob("*.md")}
    assert {"lightfall", "observer"} <= names


class _FakeRegistry:
    def __init__(self, specs):
        self._specs = specs

    def enabled_specs(self):
        return self._specs


class _FailingRegistry:
    def enabled_specs(self):
        raise RuntimeError("boom")

    def get(self, name):
        return None


class _FakeToolRegistry:
    def enabled_plugins(self):
        return []


def test_identity_preamble_contains_own_identity():
    lightfall = _spec("lightfall")
    reg = _FakeRegistry([lightfall])
    preamble = identity_preamble(lightfall, reg)
    assert "**lightfall**" in preamble
    assert "d-lightfall" in preamble
    assert 'name on the agent bus is "lightfall"' in preamble


def test_identity_preamble_is_authoritative_for_session():
    lightfall = _spec("lightfall")
    reg = _FakeRegistry([lightfall])
    preamble = identity_preamble(lightfall, reg)
    assert "authoritative for this\nsession" in preamble


def test_identity_preamble_excludes_self_from_peers():
    lightfall = _spec("lightfall")
    saxs = _spec("saxs")
    reg = _FakeRegistry([lightfall, saxs])
    preamble = identity_preamble(lightfall, reg)
    assert preamble.count("**lightfall**") == 1
    assert "**saxs**" in preamble


def test_identity_preamble_excludes_disabled_specs():
    # enabled_specs() is the source of truth; a disabled spec simply
    # never appears in the list passed to identity_preamble.
    lightfall = _spec("lightfall")
    saxs = _spec("saxs")
    reg = _FakeRegistry([lightfall, saxs])
    preamble = identity_preamble(lightfall, reg)
    assert "**saxs**" in preamble
    assert "**observer**" not in preamble


def test_identity_preamble_tags_openable_and_subagent():
    lightfall = _spec("lightfall")
    both = _spec("both", openable=True, subagent=True)
    only_openable = _spec("only_openable", openable=True, subagent=False)
    only_subagent = _spec("only_subagent", openable=False, subagent=True)
    neither = _spec("neither", openable=False, subagent=False)
    reg = _FakeRegistry([lightfall, both, only_openable, only_subagent, neither])
    preamble = identity_preamble(lightfall, reg)

    assert "**both** — d-both [openable as a session, Agent-tool delegable]" in preamble
    assert "**only_openable** — d-only_openable [openable as a session]" in preamble
    assert "**only_subagent** — d-only_subagent [Agent-tool delegable]" in preamble
    assert "**neither** — d-neither" in preamble
    assert "**neither** — d-neither [" not in preamble

    assert "mcp__bus__list_agents" in preamble
    assert "mcp__bus__send_message" in preamble
    assert 'mcp__bus__launch_agent(name="<name>"' in preamble
    assert '"+" button' in preamble
    assert "Agent tool" in preamble
    assert "snapshot from session start" in preamble
    assert "mcp__bus__list_agents" in preamble
    assert "defined after this session began" in preamble


def test_assemble_spec_options_prepends_preamble(monkeypatch, tmp_path):
    lightfall = _spec("lightfall")
    saxs = _spec("saxs")
    reg = _FakeRegistry([lightfall, saxs])
    monkeypatch.setattr("lightfall.agents.assembly.template_variables",
                        lambda: {"user": "ron", "beamline": "", "endstation": ""})
    options = assemble_spec_options(lightfall, _FakeToolRegistry(), reg, tmp_path)
    preamble = identity_preamble(lightfall, reg)
    assert options["system_prompt"].startswith(preamble)
    assert options["system_prompt"].endswith("p ron")


def test_assemble_spec_options_agy_roster_and_subagents(monkeypatch, tmp_path):
    from lightfall.agents.backends.info import AgyInfo

    lightfall = _spec("lightfall")
    reg = _FakeRegistry([lightfall, _spec("saxs", subagent=True)])
    monkeypatch.setattr("lightfall.agents.assembly.template_variables",
                        lambda: {"user": "ron", "beamline": "", "endstation": ""})
    options = assemble_spec_options(lightfall, _FakeToolRegistry(), reg, tmp_path,
                                    backend=AgyInfo())
    assert [s.name for s in options["subagent_specs"]] == ["saxs"]
    prompt = options["system_prompt"]
    assert "**saxs** — d-saxs [openable as a session, subagent delegable]" in prompt
    assert "`invoke_subagent`" in prompt
    assert "Task" not in prompt and "Agent tool" not in prompt


def test_assemble_spec_options_tolerates_registry_enumeration_failure(monkeypatch, tmp_path):
    lightfall = _spec("lightfall")
    reg = _FailingRegistry()
    monkeypatch.setattr("lightfall.agents.assembly.template_variables",
                        lambda: {"user": "ron", "beamline": "", "endstation": ""})
    options = assemble_spec_options(lightfall, _FakeToolRegistry(), reg, tmp_path)
    assert options["system_prompt"] == "p ron"


def _reg_with_delegable_peer():
    lightfall = _spec("lightfall")
    peer = _spec("saxs", openable=True, subagent=True)
    return lightfall, _FakeRegistry([lightfall, peer])


def test_preamble_agy_has_no_claude_only_wording():
    from lightfall.agents.backends.info import AgyInfo

    lightfall, reg = _reg_with_delegable_peer()
    text = identity_preamble(lightfall, reg, AgyInfo())
    assert "Agent tool" not in text and "Agent-tool" not in text
    assert "ScheduleWakeup" not in text
    assert "mcp__" not in text
    assert "`lightfall_bus` / `list_agents`" in text and "`lightfall_bus` / `launch_agent(" in text
    assert "Assistant panel's tab bar" in text


def test_preamble_agy_delegates_with_invoke_subagent():
    from lightfall.agents.backends.info import AgyInfo

    lightfall, reg = _reg_with_delegable_peer()
    text = identity_preamble(lightfall, reg, AgyInfo())
    assert "**saxs** — d-saxs [openable as a session, subagent delegable]" in text
    assert "`invoke_subagent`" in text
    assert "Task" not in text


def test_preamble_agy_without_subagent_peers_has_no_delegation_note():
    from lightfall.agents.backends.info import AgyInfo

    lightfall = _spec("lightfall")
    reg = _FakeRegistry([lightfall, _spec("saxs", openable=True, subagent=False)])
    text = identity_preamble(lightfall, reg, AgyInfo())
    assert "invoke_subagent" not in text and "delegable" not in text


def test_preamble_claude_keeps_delegation_and_notes():
    from lightfall.agents.backends.info import ClaudeCodeInfo

    lightfall, reg = _reg_with_delegable_peer()
    text = identity_preamble(lightfall, reg, ClaudeCodeInfo())
    assert "Agent-tool delegable" in text
    assert "`mcp__bus__list_agents`" in text
    assert "ScheduleWakeup" in text
    assert "Claude panel" not in text and "Assistant panel's tab bar" in text
    assert identity_preamble(lightfall, reg) == text  # None = Claude wording


def test_lightfall_spec_prompt_is_backend_neutral():
    from pathlib import Path

    import lightfall.agents as agents_pkg

    md = (Path(agents_pkg.__file__).parent / "builtin" / "lightfall.md").read_text(encoding="utf-8")
    assert "ScheduleWakeup" not in md
