"""Plan design skill plugin.

Provides Claude with expertise for designing Bluesky plans with
LUCID UI annotations for procedural UI generation.

Full API documentation is stored in skills/docs/plan_design.md and
loaded on-demand via the ncs_get_skill_docs tool.
"""

from __future__ import annotations

from typing import Any

from lucid.plugins.skill_plugin import SkillPlugin


class PlanDesignSkill(SkillPlugin):
    """Skill for designing Bluesky plans.

    This skill provides Claude with deep expertise for:
    - Bluesky plan_stubs (bps) for low-level building blocks
    - Standard Bluesky plans (bp) for high-level scan patterns
    - LUCID UI annotations for procedural UI generation
    - Best practices for plan composition and error handling

    Full API documentation is in skills/docs/plan_design.md.
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
        return "Expertise in designing Bluesky plans with LUCID UI annotations"

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

    def get_brief_description(self) -> str:
        """Return brief hint for system prompt (full docs are on-demand)."""
        return """## Bluesky Plan Design

Expert at designing Bluesky plans for LUCID with UI annotations.

**Use `ncs_get_skill_docs` tool with skill="plan_design"** to get full API reference for:
- `bluesky.plan_stubs` (bps.*) - movement, timing, reading stubs
- `bluesky.plans` (bp.*) - scan, grid_scan, count, etc.
- `lucid.ui.annotations` - Unit, Range, DeviceFilter, etc.

Key imports: `from bluesky import plan_stubs as bps, plans as bp`
"""

    def get_system_prompt(self) -> str:
        """Return the system prompt snippet for plan design expertise.

        Returns the brief description for the system prompt. Full documentation
        is available via ncs_get_skill_docs tool.
        """
        return self.get_brief_description()

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []
