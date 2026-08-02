"""Beamline alignment skill plugin.

Provides Claude with expertise for motor alignment and beam optimization tasks.
"""

from __future__ import annotations

from typing import Any

from lightfall.plugins.tool_plugin import ToolPlugin


class BeamlineAlignmentAgent(ToolPlugin):
    """Skill for beamline alignment and beam optimization.

    This skill provides Claude with domain expertise for:
    - Motor alignment procedures
    - Beam optimization strategies
    - Safe movement practices
    - Feedback signal interpretation
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this skill."""
        return "alignment"

    @property
    def display_name(self) -> str:
        """Return human-readable display name."""
        return "Beamline Alignment"

    @property
    def description(self) -> str:
        """Return description of this skill's capabilities."""
        return "Expertise in motor alignment and beam optimization procedures"

    @property
    def category(self) -> str:
        """Return category for grouping in settings UI."""
        return "operations"

    @property
    def enabled_by_default(self) -> bool:
        """Return whether this skill is enabled by default."""
        return True

    @property
    def priority(self) -> int:
        """Return priority (lower = higher in prompt order)."""
        return 10

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []
