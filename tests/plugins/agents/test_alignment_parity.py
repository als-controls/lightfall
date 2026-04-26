"""Snapshot/parity test: migrated alignment agent matches old skill content."""
from __future__ import annotations


def test_migrated_prompt_matches_legacy():
    """get_system_prompt() body must be byte-identical to the legacy skill's.

    This guards against accidental content drift during the file move.
    """
    from lucid.plugins.agents.alignment import BeamlineAlignmentAgent
    from lucid.plugins.skills.alignment import BeamlineAlignmentSkill

    new = BeamlineAlignmentAgent().get_system_prompt()
    old = BeamlineAlignmentSkill().get_system_prompt()
    assert new == old


def test_metadata_preserved():
    from lucid.plugins.agents.alignment import BeamlineAlignmentAgent

    p = BeamlineAlignmentAgent()
    assert p.name == "alignment"
    assert p.display_name == "Beamline Alignment"
    assert p.category == "operations"
    assert p.enabled_by_default is True
    assert p.priority == 10
