"""Assistant Tools settings page: which tool plugins the assistant gets, per scope.

A catalog of every ToolPlugin in ToolRegistry with a Not set / On / Off choice at the
current scope, writing the scoped, item-merged ``tools.enabled`` setting
(``{plugin name: bool}``; see ``lightfall.ui.panels.claude.tool_registry``). The Inherited
column shows what broader scopes (or the plugin's ``enabled_by_default``) give each one.

ToolRegistry reads the resolved map whenever an assistant session is built, so a change
applies to the next conversation: the field does not ask for a restart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall_settings import SettingSpec

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.panels.claude.tool_registry import TOOLS_KEY
from lightfall.ui.preferences.form.schema import Catalog, CatalogItem, Section, SettingField
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon

_SCOPES = frozenset({"user", "branch", "beamline", "facility"})


def _registered_tools() -> list[CatalogItem]:
    """One catalog item per registered tool plugin, sorted by category then display name."""
    try:
        from lightfall.ui.panels.claude.tool_registry import ToolRegistry, _migrate_tools_quietly

        registry = ToolRegistry.get_instance()
        if registry._migrate_legacy_pref_if_needed():  # as the old load_settings did
            _migrate_tools_quietly()
        plugins = registry.get_plugins()
    except Exception as e:  # noqa: BLE001 - an unavailable registry shows an empty list
        logger.warning("Failed to get ToolRegistry: {}", e)
        return []
    plugins = sorted(plugins, key=lambda p: (p.category, p.display_name))
    return [
        CatalogItem(
            id=p.name,
            label=p.display_name,
            default_enabled=p.enabled_by_default,
            columns=(p.category.title(), p.description),
        )
        for p in plugins
    ]


class ClaudeToolsSettingsPlugin(SettingsPlugin):
    """Settings page for enabling and disabling assistant tool plugins at each scope.

    Enabled plugins contribute their tools (per-plugin MCP server) and skill prompt
    (SKILL.md materialized into the per-session SDK plugin dir).
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "claude_tools"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Assistant Tools"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "advanced"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 90  # Show before Plugins

    def specs(self) -> Sequence[SettingSpec]:
        return [
            SettingSpec(key=TOOLS_KEY, type=bool, scopes=_SCOPES, policy="union",
                        label="Enabled assistant tools"),
        ]

    def sections(self) -> Sequence[Section]:
        """The one Tools section: the catalog of registered tool plugins."""
        return [
            Section(
                "Assistant tools",
                [
                    SettingField(
                        TOOLS_KEY,
                        Catalog(items=_registered_tools, columns=("Category", "Description")),
                        label="Tools",
                    )
                ],
                description=(
                    "Tools provide specific capabilities, while skills provide domain "
                    "expertise and may include additional tools. Changes take effect "
                    "for new assistant conversations."
                ),
            )
        ]


# Backwards compatibility alias
SkillSettingsPlugin = ClaudeToolsSettingsPlugin
