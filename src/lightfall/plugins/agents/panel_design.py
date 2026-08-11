"""Panel design skill plugin.

Provides Claude with expertise for designing BasePanel subclasses
for the Lightfall application. This skill teaches the full panel API
including metadata, lifecycle, state management, and self-registration.

The skill prompt and full API documentation now ship as
src/lightfall/skills/builtin/panel_design/SKILL.md (+ references/), loaded
by the shipped-skills store rather than this plugin's get_system_prompt.
"""

from __future__ import annotations

from typing import Any

from lightfall.plugins.tool_plugin import ToolPlugin


class PanelDesignAgent(ToolPlugin):
    """Skill for designing Lightfall panel plugins.

    This skill provides Claude with deep expertise for:
    - BasePanel lifecycle and API
    - PanelMetadata configuration
    - State management and introspection
    - Self-registration pattern for user plugins
    - Qt/PySide6 component patterns

    Full API documentation is in references/panel_design.md (loaded by
    the SDK's deferred Skill tool when this skill is invoked).
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this skill."""
        return "panel_design"

    @property
    def display_name(self) -> str:
        """Return human-readable display name."""
        return "Panel Design"

    @property
    def description(self) -> str:
        """Return description of this skill's capabilities."""
        return "Expertise in designing Lightfall panel plugins with self-registration"

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
        return 20

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []
