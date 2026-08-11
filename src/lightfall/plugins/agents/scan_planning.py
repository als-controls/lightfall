"""Scan planning skill plugin.

Provides Claude with expertise for planning and configuring data acquisition scans.
"""

from __future__ import annotations

from typing import Any

from lightfall.plugins.tool_plugin import ToolPlugin


class ScanPlanningAgent(ToolPlugin):
    """Skill for planning and configuring scans.

    This skill provides Claude with domain expertise for:
    - Scan type selection and configuration
    - Parameter optimization
    - Time estimation
    - Data collection strategies
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this skill."""
        return "scan_planning"

    @property
    def display_name(self) -> str:
        """Return human-readable display name."""
        return "Scan Planning"

    @property
    def description(self) -> str:
        """Return description of this skill's capabilities."""
        return "Expertise in planning and configuring data acquisition scans"

    @property
    def category(self) -> str:
        """Return category for grouping in settings UI."""
        return "analysis"

    @property
    def enabled_by_default(self) -> bool:
        """Return whether this skill is enabled by default."""
        return True

    @property
    def priority(self) -> int:
        """Return priority (lower = higher in prompt order)."""
        return 20

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []
