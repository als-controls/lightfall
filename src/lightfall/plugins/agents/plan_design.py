"""Plan design skill plugin.

Provides Claude with expertise for designing Bluesky plans with
Lightfall UI annotations for procedural UI generation.

Full API documentation is shipped as references/plan_design.md alongside
the SKILL.md and surfaced lazily by the SDK's deferred Skill tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin


class PlanDesignAgent(AgentPlugin):
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

    def get_brief_description(self) -> str:
        """Return brief hint for system prompt (full docs are on-demand)."""
        return """## Bluesky Plan Design

Expert at designing Bluesky plans for Lightfall with UI annotations.

**See `references/plan_design.md` (loaded automatically by the SDK Skill tool)** for full API reference covering:
- `bluesky.plan_stubs` (bps.*) - movement, timing, reading stubs
- `bluesky.plans` (bp.*) - scan, grid_scan, count, etc.
- `lightfall.ui.annotations` - Unit, Range, DeviceFilter, etc.

Key imports: `from bluesky import plan_stubs as bps, plans as bp`
"""

    def get_system_prompt(self) -> str:
        """Return the system prompt snippet for plan design expertise.

        Returns the brief description; full documentation in references/
        is loaded on-demand by the SDK's deferred Skill tool.
        """
        return self.get_brief_description()

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []

    def get_references_dir(self) -> Path | None:
        """Return path to the references directory containing supplementary docs."""
        return Path(__file__).parent / "plan_design" / "references"
