from pathlib import Path

import pytest

from lightfall.agents.spec import AgentSpec, AgentSpecError, parse_agent_file, resolve_template

GOOD = """---
name: saxs-specialist
description: SAXS data-quality specialist
model: sonnet
effort: medium
tools: [scan_planning, alignment]
skills: [saxs-conventions]
memory: false
lightfall:
  subagent: false
  on_message: auto
  forward_min_severity: warn
---
You are the SAXS specialist for {{beamline}}.
"""


def _write(tmp_path: Path, text: str, name: str = "a.md") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_full_frontmatter(tmp_path):
    spec = parse_agent_file(_write(tmp_path, GOOD), scope="user")
    assert spec.name == "saxs-specialist"
    assert spec.description == "SAXS data-quality specialist"
    assert spec.model == "sonnet"
    assert spec.effort == "medium"
    assert spec.tools == ("scan_planning", "alignment")
    assert spec.skills == ("saxs-conventions",)
    assert spec.memory is False
    assert spec.subagent is False
    assert spec.on_message == "auto"
    assert spec.forward_min_severity == "warn"
    assert spec.scope == "user"
    assert spec.prompt.startswith("You are the SAXS specialist")


def test_parse_minimal_defaults(tmp_path):
    text = "---\nname: mini\ndescription: d\n---\nbody"
    spec = parse_agent_file(_write(tmp_path, text), scope="core")
    assert spec.tools == ()
    assert spec.memory is True
    assert spec.subagent is True
    assert spec.on_message == "queue"
    assert spec.forward_min_severity is None


@pytest.mark.parametrize("text", [
    "no frontmatter at all",
    "---\ndescription: missing name\n---\nbody",
    "---\nname: x\n---\nbody",                      # missing description
    "---\nname: x\ndescription: d\n---\n",          # empty body
    "---\nname: x\ndescription: d\nlightfall:\n  on_message: shout\n---\nb",  # bad enum
])
def test_parse_rejects(tmp_path, text):
    with pytest.raises(AgentSpecError):
        parse_agent_file(_write(tmp_path, text), scope="user")


def test_unknown_top_level_key_warns_not_raises(tmp_path):
    text = "---\nname: x\ndescription: d\nfuture_key: 1\n---\nbody"
    spec = parse_agent_file(_write(tmp_path, text), scope="user")  # must not raise
    assert spec.name == "x"


def test_resolve_template():
    assert resolve_template("hi {{beamline}}", {"beamline": "7.0.1"}) == "hi 7.0.1"


def test_resolve_template_unknown_var_raises():
    with pytest.raises(AgentSpecError):
        resolve_template("hi {{nope}}", {"beamline": "7.0.1"})
