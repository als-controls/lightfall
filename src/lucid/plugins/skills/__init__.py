"""Built-in skill plugins for Claude assistant.

This package contains example skill plugins that provide domain expertise
and tools for the Claude assistant.
"""

from lucid.plugins.skills.alignment import BeamlineAlignmentSkill
from lucid.plugins.skills.scan_planning import ScanPlanningSkill

__all__ = [
    "BeamlineAlignmentSkill",
    "ScanPlanningSkill",
]
