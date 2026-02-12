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

import re
from abc import abstractmethod
from pathlib import Path
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

    def get_brief_description(self) -> str:
        """Get a brief (~100-200 char) description for the system prompt.

        This is a short hint that goes in the system prompt, telling Claude
        what the skill does without the full API documentation. Override
        this for skills with large documentation files to keep the system
        prompt small.

        By default, returns the full system prompt for backward compatibility.

        Returns:
            Brief description text for the system prompt.
        """
        return self.get_system_prompt()

    def get_documentation_path(self) -> Path | None:
        """Get the path to the documentation markdown file for this skill.

        Skills with large API documentation should have a corresponding
        markdown file in the skills/docs/ directory. The filename should
        match the skill name (e.g., "plan_design" -> "plan_design.md").

        Returns:
            Path to the documentation file, or None if no external docs.
        """
        docs_dir = Path(__file__).parent / "skills" / "docs"
        doc_file = docs_dir / f"{self.name}.md"
        if doc_file.exists():
            return doc_file
        return None

    def get_documentation_topics(self) -> list[str]:
        """Extract topic slugs from H2 headers in the documentation.

        Parses the documentation file and returns slugified versions of
        all H2 (##) headers. These can be used with get_documentation(topic)
        to retrieve specific sections.

        Returns:
            List of topic slugs, or empty list if no documentation.
        """
        doc_path = self.get_documentation_path()
        if doc_path is None:
            return []

        try:
            content = doc_path.read_text(encoding="utf-8")
        except Exception:
            return []

        # Find all H2 headers (## Header Text)
        topics = []
        for match in re.finditer(r"^## (.+)$", content, re.MULTILINE):
            header = match.group(1).strip()
            # Slugify: lowercase, replace spaces with hyphens
            slug = re.sub(r"[^a-z0-9]+", "-", header.lower()).strip("-")
            if slug:
                topics.append(slug)

        return topics

    def get_documentation(self, topic: str | None = None) -> str | None:
        """Get documentation content, optionally for a specific topic.

        Args:
            topic: Optional topic slug to retrieve a specific section.
                   If None, returns the full documentation.

        Returns:
            Documentation text, or None if no documentation available.
        """
        doc_path = self.get_documentation_path()
        if doc_path is None:
            return None

        try:
            content = doc_path.read_text(encoding="utf-8")
        except Exception:
            return None

        if topic is None:
            return content

        # Find the section for this topic
        # Look for ## header that slugifies to the requested topic
        lines = content.split("\n")
        section_lines = []
        in_section = False
        current_slug = None

        for line in lines:
            # Check if this is an H2 header
            h2_match = re.match(r"^## (.+)$", line)
            if h2_match:
                header = h2_match.group(1).strip()
                current_slug = re.sub(r"[^a-z0-9]+", "-", header.lower()).strip("-")

                if in_section:
                    # We hit the next section, stop
                    break

                if current_slug == topic:
                    in_section = True
                    section_lines.append(line)
                continue

            # Check for H1 header (## -> end of all H2 sections under it)
            if re.match(r"^# ", line) and in_section:
                break

            if in_section:
                section_lines.append(line)

        if section_lines:
            return "\n".join(section_lines).strip()

        return None

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
