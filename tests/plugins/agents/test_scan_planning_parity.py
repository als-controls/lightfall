"""Parity test: migrated scan_planning agent matches old skill content."""
from __future__ import annotations


def test_migrated_prompt_matches_legacy():
    from lucid.plugins.agents.scan_planning import ScanPlanningAgent
    from lucid.plugins.skills.scan_planning import ScanPlanningSkill

    assert ScanPlanningAgent().get_system_prompt() == ScanPlanningSkill().get_system_prompt()


def test_metadata_preserved():
    from lucid.plugins.agents.scan_planning import ScanPlanningAgent

    p = ScanPlanningAgent()
    assert p.name == "scan_planning"
    assert p.display_name == "Scan Planning"
    assert p.category == "analysis"
    assert p.enabled_by_default is True
    assert p.priority == 20
