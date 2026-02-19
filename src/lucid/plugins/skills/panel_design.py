"""Panel design skill plugin.

Provides Claude with expertise for designing BasePanel subclasses
for the LUCID application. This skill teaches the full panel API
including metadata, lifecycle, state management, and self-registration.

Full API documentation is stored in skills/docs/panel_design.md and
loaded on-demand via the ncs_get_skill_docs tool.
"""

from __future__ import annotations

from typing import Any

from lucid.plugins.skill_plugin import SkillPlugin


class PanelDesignSkill(SkillPlugin):
    """Skill for designing LUCID panel plugins.

    This skill provides Claude with deep expertise for:
    - BasePanel lifecycle and API
    - PanelMetadata configuration
    - State management and introspection
    - Self-registration pattern for user plugins
    - Qt/PySide6 component patterns

    Full API documentation is in skills/docs/panel_design.md.
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
        return "Expertise in designing LUCID panel plugins with self-registration"

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

    def get_brief_description(self) -> str:
        """Return brief hint for system prompt (full docs are on-demand)."""
        return """## LUCID Panel Design

Expert at designing Qt panel plugins for LUCID with self-registration.

**Use `ncs_get_skill_docs` tool with skill="panel_design"** to get full API reference for:
- `PanelMetadata` - id, name, category, docking preferences
- `BasePanel` lifecycle - _setup_ui(), signals, state management
- MCP introspection - _get_specific_introspection_data(), actions
- Self-registration pattern with `PanelRegistry.get_instance().register()`
- Qt widgets and layout patterns

Key imports: `from lucid.ui.panels.base import BasePanel, PanelMetadata`
"""

    def get_system_prompt(self) -> str:
        """Return the system prompt snippet for panel design expertise.

        Returns the brief description for the system prompt. Full documentation
        is available via ncs_get_skill_docs tool.
        """
        return self.get_brief_description()

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []
