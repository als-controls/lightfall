"""Parity test: migrated plan_design agent matches old skill content."""
from __future__ import annotations


def test_migrated_prompt_matches_legacy():
    from lucid.plugins.agents.plan_design import PlanDesignAgent
    from lucid.plugins.skills.plan_design import PlanDesignSkill

    assert PlanDesignAgent().get_system_prompt() == PlanDesignSkill().get_system_prompt()


def test_metadata_preserved():
    from lucid.plugins.agents.plan_design import PlanDesignAgent

    p = PlanDesignAgent()
    assert p.name == "plan_design"
    assert p.display_name == "Bluesky Plan Design"
    assert p.category == "development"
    assert p.enabled_by_default is True
    assert p.priority == 15


def test_references_dir_points_to_migrated_doc():
    from lucid.plugins.agents.plan_design import PlanDesignAgent

    p = PlanDesignAgent()
    refs = p.get_references_dir()
    assert refs is not None
    assert refs.is_dir()
    assert (refs / "plan_design.md").exists()
