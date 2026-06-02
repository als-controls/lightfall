"""Panel design skill plugin.

Provides Claude with expertise for designing BasePanel subclasses
for the LUCID application. This skill teaches the full panel API
including metadata, lifecycle, state management, and self-registration.

Full API documentation is shipped as references/panel_design.md alongside
the SKILL.md and surfaced lazily by the SDK's deferred Skill tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin


class PanelDesignAgent(AgentPlugin):
    """Skill for designing LUCID panel plugins.

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

**See `references/panel_design.md` (loaded automatically by the SDK Skill tool)** for full API reference covering:
- `PanelMetadata` - id, name, category, docking preferences
- `BasePanel` lifecycle - _setup_ui(), signals, state management
- MCP introspection - _get_specific_introspection_data(), actions
- User-plugin auto-registration via `PluginType.__init_subclass__`
- Qt widgets and layout patterns

Key imports: `from lightfall.ui.panels.base import BasePanel, PanelMetadata`
"""

    def get_system_prompt(self) -> str:
        """Return the system prompt snippet for panel design expertise.

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
        return Path(__file__).parent / "panel_design" / "references"
