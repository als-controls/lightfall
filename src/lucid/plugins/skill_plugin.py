"""Skill plugin type for Claude assistant capabilities.

SkillPlugin extends MCPToolPlugin with system prompt capabilities.
Skills combine system prompt snippets (instructional context) with optional
MCP tools, allowing modular expertise packages for specific domains
(e.g., beamline alignment, data analysis, scan planning).

The inheritance hierarchy is:
    PluginType -> MCPToolPlugin -> SkillPlugin

This allows all tool plugins (mcp_tool and skill) to be managed via
the same settings UI with enable/disable support.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from lucid.plugins.mcp_tool import MCPToolPlugin

if TYPE_CHECKING:
    pass


class SkillPlugin(MCPToolPlugin):
    """Abstract base for skill plugins.

    Skill plugins extend MCPToolPlugin with system prompt capabilities.
    Each skill provides:
    - A system prompt snippet that gives Claude domain expertise
    - Optional MCP tools for specialized operations

    Skills differ from regular MCP tool plugins in that:
    - They provide get_system_prompt() for domain expertise
    - They are disabled by default (enabled_by_default = False)
    - They are registered with both MCPToolRegistry and SkillRegistry

    Skills are:
    - Discovered via plugin manifests (same as other plugins)
    - Registered with MCPToolRegistry (for tools) and SkillRegistry (for prompts)
    - Enabled/disabled per-user via preferences
    - Aggregated by priority into Claude's context

    Class Attributes:
        type_name: "skill" - identifies this as a skill plugin.
        is_singleton: True - skill plugins are singletons.

    Inherited from MCPToolPlugin:
        name: Unique identifier (abstract)
        description: Human-readable description (abstract)
        display_name: Name for settings UI (default: name.title())
        category: Grouping category (default: "general")
        enabled_by_default: Whether on by default (overridden to False for skills)
        priority: Sort order (default: 100)
        create_tools(): Create MCP tools (default: empty list)

    Skill-specific:
        get_system_prompt(): System prompt snippet (abstract)

    Example implementation::

        class BeamlineAlignmentSkill(SkillPlugin):
            @property
            def name(self) -> str:
                return "alignment"

            @property
            def description(self) -> str:
                return "Expertise in motor alignment and beam optimization"

            @property
            def category(self) -> str:
                return "operations"

            @property
            def enabled_by_default(self) -> bool:
                return True  # Override default False if desired

            @property
            def priority(self) -> int:
                return 10

            def get_system_prompt(self) -> str:
                return '''
                ## Beamline Alignment Expertise
                When helping with alignment:
                - Check current positions before suggesting moves
                - Use small incremental moves for fine alignment
                - Monitor feedback signals when available
                '''

            def create_tools(self) -> list[Any]:
                return []  # Or return alignment-specific tools
    """

    type_name: ClassVar[str] = "skill"
    is_singleton: ClassVar[bool] = True

    # Override: skills are disabled by default (opt-in)
    @property
    def enabled_by_default(self) -> bool:
        """Whether this skill is enabled by default.

        Skills are disabled by default (opt-in). Override this to True
        for skills that should be active for new users without explicit
        configuration.

        Returns:
            True if enabled by default. Defaults to False for skills.
        """
        return False

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Get the system prompt snippet for this skill.

        This text is appended to Claude's system prompt when the skill
        is enabled. It should provide domain expertise and guidance.

        Returns:
            System prompt text for this skill.
        """
        ...

    def create_tools(self) -> list[Any]:
        """Create MCP tools provided by this skill.

        Override this to return a list of tool instances that should be
        registered when the skill is enabled. Tools should follow the
        MCP tool interface.

        Returns:
            List of tool instances, or empty list if no tools.
        """
        return []

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with skill plugin information.
        """
        # Get base data from parent
        data = super().get_introspection_data()
        # Add skill-specific fields
        data["has_tools"] = len(self.create_tools()) > 0
        data["has_prompt"] = bool(self.get_system_prompt().strip())
        return data
