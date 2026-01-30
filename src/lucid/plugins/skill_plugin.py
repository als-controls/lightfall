"""Skill plugin type for Claude assistant capabilities.

SkillPlugin defines contextual expertise packages for the Claude assistant.
Skills combine system prompt snippets (instructional context) with optional
MCP tools, allowing modular expertise packages for specific domains
(e.g., beamline alignment, data analysis, scan planning).
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from lucid.plugins.types import PluginType

if TYPE_CHECKING:
    pass


class SkillPlugin(PluginType):
    """Abstract base for skill plugins.

    Skill plugins extend the Claude assistant with contextual capabilities.
    Each skill provides:
    - A system prompt snippet that gives Claude domain expertise
    - Optional MCP tools for specialized operations

    Skills are:
    - Discovered via plugin manifests (same as other plugins)
    - Registered with SkillRegistry on load
    - Enabled/disabled per-user via preferences
    - Aggregated by priority into Claude's context

    Class Attributes:
        type_name: "skill" - identifies this as a skill plugin.
        is_singleton: True - skill plugins are singletons.

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
                return True

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
    description: ClassVar[str] = "Claude assistant skill plugin"
    is_singleton: ClassVar[bool] = True

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this skill.

        This should be unique within the skill type and is used to
        identify the skill in the registry and preferences.
        """
        ...

    @property
    @abstractmethod
    def description(self) -> str:  # noqa: F811
        """Human-readable description of what this skill provides.

        This is shown in the settings UI to help users understand
        what enabling this skill will do.
        """
        ...

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Get the system prompt snippet for this skill.

        This text is appended to Claude's system prompt when the skill
        is enabled. It should provide domain expertise and guidance.

        Returns:
            System prompt text for this skill.
        """
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name shown in settings UI.

        Override this to provide a custom display name. By default,
        converts the name to title case.

        Returns:
            Display name for the settings UI.
        """
        return self.name.replace("_", " ").title()

    @property
    def category(self) -> str:
        """Category for grouping skills in the settings UI.

        Override this to group related skills together.
        Common categories: "general", "operations", "analysis", "planning".

        Returns:
            Category name. Defaults to "general".
        """
        return "general"

    @property
    def enabled_by_default(self) -> bool:
        """Whether this skill is enabled by default.

        Override this to True for skills that should be active
        for new users without explicit configuration.

        Returns:
            True if enabled by default. Defaults to False.
        """
        return False

    @property
    def priority(self) -> int:
        """Sort order for system prompt aggregation (lower = higher priority).

        Skills with lower priority values have their prompts appear earlier
        in the aggregated system prompt. Use this to ensure important
        context appears first.

        Returns:
            Priority value. Defaults to 100.
        """
        return 100

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
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "enabled_by_default": self.enabled_by_default,
            "priority": self.priority,
            "has_tools": len(self.create_tools()) > 0,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
