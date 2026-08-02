from pathlib import Path

from lightfall.agents.assembly import agent_cwd, assemble_spec_options, subagent_definitions
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
