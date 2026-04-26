"""Parity test: migrated panel_design agent matches old skill content."""
from __future__ import annotations


def test_migrated_prompt_matches_legacy():
    from lucid.plugins.agents.panel_design import PanelDesignAgent
    from lucid.plugins.skills.panel_design import PanelDesignSkill

    assert PanelDesignAgent().get_system_prompt() == PanelDesignSkill().get_system_prompt()


def test_metadata_preserved():
    from lucid.plugins.agents.panel_design import PanelDesignAgent

    p = PanelDesignAgent()
    assert p.name == "panel_design"
    assert p.display_name == "Panel Design"
    assert p.category == "development"
    assert p.enabled_by_default is True
    assert p.priority == 20
