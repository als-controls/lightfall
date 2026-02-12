"""MCP tools for on-demand skill documentation retrieval.

Provides tools for Claude to retrieve detailed API documentation for skills
only when needed, rather than loading all documentation into the system prompt.

This reduces token usage by keeping system prompts brief and loading full
API documentation on-demand when Claude is actually working on a task that
requires it.
"""

from __future__ import annotations

from typing import Any

from lucid.plugins.mcp_tool import MCPToolPlugin
from lucid.plugins.tools._mcp_helpers import mcp_result
from lucid.utils.logging import logger


class SkillDocsToolPlugin(MCPToolPlugin):
    """MCP tools for on-demand skill documentation retrieval.

    This plugin provides tools for Claude to:
    - List available skills and their documentation topics
    - Retrieve full or topic-specific documentation for skills

    Skills with large API documentation (like plan_design and panel_design)
    store their docs in markdown files. This allows the system prompt to
    contain only brief hints, with full documentation loaded on-demand.
    """

    @property
    def name(self) -> str:
        """Plugin name."""
        return "skill_docs"

    @property
    def description(self) -> str:
        """Human-readable description of what this plugin provides."""
        return "Tools for retrieving skill API documentation on-demand"

    @property
    def category(self) -> str:
        """Category for grouping in settings UI."""
        return "development"

    @property
    def enabled_by_default(self) -> bool:
        """Enable by default since it's needed for skill documentation."""
        return True

    @property
    def priority(self) -> int:
        """Higher priority to appear early in tool list."""
        return 5

    def _get_skill_registry(self):
        """Get the skill registry instance."""
        from lucid.ui.panels.claude.skill_registry import SkillRegistry

        return SkillRegistry.get_instance()

    def create_tools(self) -> list[Any]:
        """Create skill documentation MCP tools.

        Returns:
            List of tool functions.
        """
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning("claude_agent_sdk not available, skill_docs tools disabled")
            return []

        @tool(
            name="ncs_list_skills",
            description=(
                "List all registered skills with their documentation topics. "
                "Use this to discover what skills are available and what topics "
                "each skill's documentation covers. Returns skill names, descriptions, "
                "enabled status, and available documentation topics."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "enabled_only": {
                        "type": "boolean",
                        "description": "Only list enabled skills (default: false)",
                        "default": False,
                    },
                },
            },
        )
        async def list_skills(args: dict) -> dict[str, Any]:
            """List all skills with their documentation topics."""
            enabled_only = args.get("enabled_only", False)

            registry = self._get_skill_registry()
            enabled_names = registry._get_enabled_skill_names()

            skills_data = []
            for skill in registry.get_all_skills():
                is_enabled = skill.name in enabled_names

                if enabled_only and not is_enabled:
                    continue

                # Get documentation topics
                topics = skill.get_documentation_topics()
                has_docs = skill.get_documentation_path() is not None

                skills_data.append({
                    "name": skill.name,
                    "display_name": skill.display_name,
                    "description": skill.description,
                    "category": skill.category,
                    "enabled": is_enabled,
                    "has_documentation": has_docs,
                    "topics": topics if has_docs else [],
                })

            # Sort by priority (enabled first, then by name)
            skills_data.sort(key=lambda s: (not s["enabled"], s["name"]))

            return mcp_result({
                "success": True,
                "count": len(skills_data),
                "skills": skills_data,
                "hint": (
                    "Use ncs_get_skill_docs with a skill name to retrieve its "
                    "full documentation, or specify a topic to get just that section."
                ),
            })

        @tool(
            name="ncs_get_skill_docs",
            description=(
                "Retrieve API documentation for a specific skill. "
                "Skills like 'plan_design' and 'panel_design' have detailed API "
                "references for Bluesky plans and Qt panels respectively. "
                "You can retrieve the full documentation or a specific topic section. "
                "Use ncs_list_skills first to see available skills and their topics."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": (
                            "The skill name (e.g., 'plan_design', 'panel_design')"
                        ),
                    },
                    "topic": {
                        "type": "string",
                        "description": (
                            "Optional topic to retrieve a specific section "
                            "(e.g., 'bluesky-plan-stubs', 'panel-metadata'). "
                            "If not specified, returns full documentation."
                        ),
                    },
                    "list_topics": {
                        "type": "boolean",
                        "description": (
                            "If true, only return the list of available topics "
                            "without the full documentation content (default: false)"
                        ),
                        "default": False,
                    },
                },
                "required": ["skill"],
            },
        )
        async def get_skill_docs(args: dict) -> dict[str, Any]:
            """Get documentation for a specific skill."""
            skill_name = args["skill"]
            topic = args.get("topic")
            list_topics = args.get("list_topics", False)

            registry = self._get_skill_registry()
            skill = registry.get_skill(skill_name)

            if skill is None:
                # List available skills in error message
                available = [s.name for s in registry.get_all_skills()]
                return mcp_result({
                    "success": False,
                    "error": f"Skill '{skill_name}' not found",
                    "available_skills": available,
                })

            # Get documentation topics
            topics = skill.get_documentation_topics()
            has_docs = skill.get_documentation_path() is not None

            if list_topics:
                return mcp_result({
                    "success": True,
                    "skill": skill_name,
                    "display_name": skill.display_name,
                    "description": skill.description,
                    "has_documentation": has_docs,
                    "topics": topics,
                })

            if not has_docs:
                # No external documentation, return the system prompt
                prompt = skill.get_system_prompt()
                return mcp_result({
                    "success": True,
                    "skill": skill_name,
                    "display_name": skill.display_name,
                    "description": skill.description,
                    "has_documentation": False,
                    "content": prompt,
                    "note": (
                        "This skill does not have external documentation. "
                        "The content above is from its system prompt."
                    ),
                })

            # Get documentation content
            if topic:
                content = skill.get_documentation(topic)
                if content is None:
                    return mcp_result({
                        "success": False,
                        "error": f"Topic '{topic}' not found in {skill_name} documentation",
                        "available_topics": topics,
                    })
            else:
                content = skill.get_documentation()

            if content is None:
                return mcp_result({
                    "success": False,
                    "error": f"Could not read documentation for skill '{skill_name}'",
                })

            result = {
                "success": True,
                "skill": skill_name,
                "display_name": skill.display_name,
            }

            if topic:
                result["topic"] = topic
                result["available_topics"] = topics

            result["content"] = content

            logger.debug(
                "Retrieved {} documentation for skill '{}': {} chars",
                f"topic '{topic}'" if topic else "full",
                skill_name,
                len(content),
            )

            return mcp_result(result)

        return [list_skills, get_skill_docs]
