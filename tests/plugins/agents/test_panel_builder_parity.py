"""Parity test for the migrated panel_builder agent."""
from __future__ import annotations


def test_prompt_matches_legacy():
    from lucid.plugins.agents.panel_builder import PanelBuilderAgent
    from lucid.plugins.skills.panel_builder import PanelBuilderSkill

    assert PanelBuilderAgent().get_system_prompt() == PanelBuilderSkill().get_system_prompt()


def test_tool_names_match_legacy():
    """The 5 tools have the same names as before."""
    from lucid.plugins.agents.panel_builder import PanelBuilderAgent
    from lucid.plugins.skills.panel_builder import PanelBuilderSkill

    new_tools = PanelBuilderAgent().create_tools()
    old_tools = PanelBuilderSkill().create_tools()
    new_names = sorted(getattr(t, "name", None) or t.__name__ for t in new_tools)
    old_names = sorted(getattr(t, "name", None) or t.__name__ for t in old_tools)
    assert new_names == old_names
    assert len(new_tools) == 5


def test_metadata_preserved():
    from lucid.plugins.agents.panel_builder import PanelBuilderAgent

    p = PanelBuilderAgent()
    assert p.name == "panel_builder"
    assert p.display_name == "Panel Builder"
    assert p.category == "development"
    assert p.enabled_by_default is True
    assert p.priority == 25
