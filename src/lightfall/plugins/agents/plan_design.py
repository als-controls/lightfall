"""Plan design skill plugin.

Provides Claude with expertise for designing Bluesky plans with
Lightfall UI annotations for procedural UI generation.

The skill prompt and full API documentation now ship as
src/lightfall/skills/builtin/plan_design/SKILL.md (+ references/), loaded
by the shipped-skills store rather than this plugin's get_system_prompt.
"""

from __future__ import annotations

from typing import Any

from lightfall.plugins.tool_plugin import ToolPlugin


class PlanDesignAgent(ToolPlugin):
    """Skill for designing Bluesky plans.

    This skill provides Claude with deep expertise for:
    - Bluesky plan_stubs (bps) for low-level building blocks
    - Standard Bluesky plans (bp) for high-level scan patterns
    - Lightfall UI annotations for procedural UI generation
    - Best practices for plan composition and error handling

    Full API documentation is in references/plan_design.md (loaded by
    the SDK's deferred Skill tool when this skill is invoked).
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this skill."""
        return "plan_design"

    @property
    def display_name(self) -> str:
        """Return human-readable display name."""
        return "Bluesky Plan Design"

    @property
    def description(self) -> str:
        """Return description of this skill's capabilities."""
        return "Expertise in designing Bluesky plans with Lightfall UI annotations"

    @property
    def category(self) -> str:
        """Return category for grouping in settings UI."""
        return "development"

    @property
    def enabled_by_default(self) -> bool:
        """Return whether this skill is enabled by default."""
        return True

    @property
    def priority(self) -> int:
        """Return priority (lower = higher in prompt order)."""
        return 15

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []
