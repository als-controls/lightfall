"""Parity test: migrated plan_tools agent matches old plugin's tools."""
from __future__ import annotations


def test_tool_names_match_legacy():
    from lucid.plugins.agents.plan_tools import PlanToolsAgent
    from lucid.plugins.tools.plan_tools import PlanToolPlugin

    new_names = sorted(getattr(t, "name", None) or t.__name__ for t in PlanToolsAgent().create_tools())
    old_names = sorted(getattr(t, "name", None) or t.__name__ for t in PlanToolPlugin().create_tools())
    assert new_names == old_names
    assert len(new_names) == 6


def test_metadata_preserved():
    from lucid.plugins.agents.plan_tools import PlanToolsAgent

    p = PlanToolsAgent()
    assert p.name == "plan_tools"
    assert p.category == "acquisition"


def test_get_system_prompt_empty():
    """Pure-tools plugin contributes no skill prompt."""
    from lucid.plugins.agents.plan_tools import PlanToolsAgent
    assert PlanToolsAgent().get_system_prompt() == ""
