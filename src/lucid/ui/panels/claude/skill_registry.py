"""Skill Registry for aggregating Claude assistant skills.

The SkillRegistry is a singleton that:
1. Collects skills registered by the plugin loader
2. Respects enabled/disabled state from user preferences
3. Aggregates system prompts and tools from enabled skills
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.plugins.skill_plugin import SkillPlugin


class SkillRegistry:
    """Registry for aggregating Claude assistant skills.

    This singleton collects skill plugins and provides aggregated
    system prompts and tools to the Claude panel based on user preferences.

    Skills are:
    - Registered by the plugin loader when skill plugins load
    - Enabled/disabled based on 'enabled_skills' preference
    - Aggregated by priority (lower priority = appears first in prompt)

    Example::

        registry = SkillRegistry.get_instance()

        # Skills are registered automatically by plugin loader
        # registry.register_plugin(skill_plugin)

        # Get aggregated prompt for Claude's system message
        prompt = registry.get_aggregated_system_prompt()

        # Get tools from enabled skills
        tools = registry.get_aggregated_tools()

        # Invalidate cache when preferences change
        registry.invalidate_cache()
    """

    _instance: SkillRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        """Initialize the registry.

        Use get_instance() to get the singleton instance.
        """
        self._skill_plugins: dict[str, SkillPlugin] = {}
        self._cached_prompt: str | None = None
        self._cached_tools: list[Any] | None = None

    @classmethod
    def get_instance(cls) -> SkillRegistry:
        """Get the singleton instance.

        Returns:
            The SkillRegistry instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None

    def register_plugin(self, plugin: SkillPlugin) -> None:
        """Register a skill plugin.

        Args:
            plugin: The SkillPlugin instance to register.
        """
        name = plugin.name
        if name in self._skill_plugins:
            logger.warning("Skill '{}' already registered, replacing", name)

        self._skill_plugins[name] = plugin
        self.invalidate_cache()
        logger.debug(
            "Registered skill plugin: {} (category={}, priority={})",
            name,
            plugin.category,
            plugin.priority,
        )

    def unregister_plugin(self, name: str) -> bool:
        """Unregister a skill plugin.

        Args:
            name: The skill name to unregister.

        Returns:
            True if the skill was found and removed.
        """
        if name in self._skill_plugins:
            del self._skill_plugins[name]
            self.invalidate_cache()
            logger.debug("Unregistered skill plugin: {}", name)
            return True
        return False

    def invalidate_cache(self) -> None:
        """Invalidate cached aggregations.

        Call this when preferences change to force recalculation.
        """
        self._cached_prompt = None
        self._cached_tools = None
        logger.debug("Skill registry cache invalidated")

    def _get_enabled_skill_names(self) -> set[str]:
        """Get the set of enabled skill names from preferences.

        Uses the unified enabled_tool_plugins preference shared with
        MCPToolRegistry. This ensures skills are enabled/disabled via
        the same settings UI as regular tool plugins.

        Returns:
            Set of skill names that are enabled.
        """
        try:
            from lucid.ui.panels.claude.tool_registry import MCPToolRegistry
            from lucid.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            enabled_list = prefs.get(MCPToolRegistry.ENABLED_PLUGINS_PREF)

            if enabled_list is None:
                # No preference set - use default enabled state from plugins
                return {
                    name
                    for name, plugin in self._skill_plugins.items()
                    if plugin.enabled_by_default
                }

            if isinstance(enabled_list, list):
                # Filter to only skills we know about
                return set(enabled_list) & set(self._skill_plugins.keys())

        except Exception as e:
            logger.debug("Could not load enabled plugins preference: {}", e)

        return set()

    def _get_enabled_skills(self) -> list[SkillPlugin]:
        """Get list of enabled skills, sorted by priority.

        Returns:
            List of enabled SkillPlugin instances, sorted by priority (ascending).
        """
        enabled_names = self._get_enabled_skill_names()

        enabled_skills = [
            plugin
            for name, plugin in self._skill_plugins.items()
            if name in enabled_names
        ]

        # Sort by priority (lower = higher priority, appears first)
        enabled_skills.sort(key=lambda p: p.priority)

        return enabled_skills

    def get_aggregated_system_prompt(self) -> str:
        """Get the combined system prompt from all enabled skills.

        The prompts are sorted by priority (lower = appears first) and
        joined with newlines.

        Returns:
            Combined system prompt text from enabled skills.
        """
        if self._cached_prompt is not None:
            return self._cached_prompt

        enabled_skills = self._get_enabled_skills()

        if not enabled_skills:
            self._cached_prompt = ""
            return ""

        prompts = []
        for skill in enabled_skills:
            try:
                prompt = skill.get_system_prompt()
                if prompt and prompt.strip():
                    prompts.append(prompt.strip())
            except Exception as e:
                logger.error(
                    "Failed to get system prompt from skill '{}': {}",
                    skill.name,
                    e,
                )

        self._cached_prompt = "\n\n".join(prompts)

        if prompts:
            logger.debug(
                "Aggregated system prompts from {} skills: {} chars",
                len(prompts),
                len(self._cached_prompt),
            )

        return self._cached_prompt

    def get_aggregated_tools(self) -> list[Any]:
        """Get all tools from enabled skills.

        Returns:
            List of tool instances from enabled skills.
        """
        if self._cached_tools is not None:
            return self._cached_tools

        enabled_skills = self._get_enabled_skills()

        tools = []
        for skill in enabled_skills:
            try:
                skill_tools = skill.create_tools()
                if skill_tools:
                    tools.extend(skill_tools)
                    logger.debug(
                        "Collected {} tools from skill '{}'",
                        len(skill_tools),
                        skill.name,
                    )
            except Exception as e:
                logger.error(
                    "Failed to get tools from skill '{}': {}",
                    skill.name,
                    e,
                )

        self._cached_tools = tools

        if tools:
            logger.debug("Aggregated {} tools from skills", len(tools))

        return self._cached_tools

    def get_all_skills(self) -> list[SkillPlugin]:
        """Get all registered skill plugins.

        Returns:
            List of all registered SkillPlugin instances.
        """
        return list(self._skill_plugins.values())

    def get_skill(self, name: str) -> SkillPlugin | None:
        """Get a skill by name.

        Args:
            name: The skill name.

        Returns:
            The SkillPlugin instance or None if not found.
        """
        return self._skill_plugins.get(name)

    def is_skill_enabled(self, name: str) -> bool:
        """Check if a skill is enabled.

        Args:
            name: The skill name.

        Returns:
            True if the skill is enabled.
        """
        return name in self._get_enabled_skill_names()

    @property
    def skill_count(self) -> int:
        """Get the number of registered skills."""
        return len(self._skill_plugins)

    @property
    def enabled_skill_count(self) -> int:
        """Get the number of enabled skills."""
        return len(self._get_enabled_skill_names())

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with registry state and skill information.
        """
        enabled_names = self._get_enabled_skill_names()

        return {
            "skill_count": len(self._skill_plugins),
            "enabled_count": len(enabled_names),
            "skills": [
                {
                    **plugin.get_introspection_data(),
                    "enabled": plugin.name in enabled_names,
                }
                for plugin in self._skill_plugins.values()
            ],
        }

    def get_skill_reminder(self) -> str:
        """Generate a summary of enabled skills and their capabilities.

        This creates a plain-text summary that tells Claude what skills
        are active and what tools they provide. Unlike Claude Code's
        Skill tool pattern, these skills are pre-loaded - their prompts
        and tools are already in the context.

        Returns:
            A formatted summary of enabled skills, or empty string if none.
        """
        enabled_skills = self._get_enabled_skills()

        if not enabled_skills:
            return ""

        # Build skill summary with tools info
        skill_entries = []
        for skill in enabled_skills:
            tools = skill.create_tools()
            if tools:
                tool_names = []
                for t in tools:
                    name = getattr(t, 'name', None) or getattr(t, '__name__', '?')
                    tool_names.append(name)
                tools_str = f" (provides tools: {', '.join(tool_names)})"
            else:
                tools_str = " (guidance only)"
            entry = f"- **{skill.display_name}**: {skill.description}{tools_str}"
            skill_entries.append(entry)

        skills_list = "\n".join(skill_entries)

        reminder = f"""## Active Skills Summary

The following skills are enabled and their capabilities are available to you:

{skills_list}

Skills marked "(guidance only)" provide domain expertise in the prompts above.
Skills with "(provides tools: ...)" have registered those tools - use them directly."""

        logger.debug(
            "Generated skill summary with {} skills",
            len(enabled_skills),
        )

        return reminder
