"""Plugins settings page: which plugins load, per scope.

A catalog of every discovered plugin with a Not set / On / Off choice at the current scope,
writing the scoped, item-merged ``plugins.enabled`` setting (``{unique_id: bool}``; see
``lightfall.plugins.enablement``). The Inherited column shows what broader scopes (or the
code default) give each plugin. The Status column is the plugin's live load status. The
page itself (``settings:plugins``) is protected: it is always on.

Enablement changes apply when Lightfall restarts, so the field asks for a restart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.core.services import ServiceRegistry
from lightfall.plugins.enablement import PLUGINS_KEY, PROTECTED_PLUGIN_IDS, default_enabled
from lightfall.plugins.registry import PluginRegistry
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import Catalog, CatalogItem, Section, SettingField
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon


def _discovered_plugins() -> list[CatalogItem]:
    """One catalog item per discovered plugin, sorted by type then name."""
    try:
        registry = ServiceRegistry.get_instance().get(PluginRegistry)
        plugins = registry.get_all() if registry is not None else []
        if registry is None:
            logger.warning("PluginRegistry not registered")
    except Exception as e:  # noqa: BLE001 - an unavailable registry shows an empty list
        logger.warning("Failed to get PluginRegistry: {}", e)
        plugins = []
    plugins = sorted(plugins, key=lambda p: (p.type_name, p.name))
    return [
        CatalogItem(
            id=p.unique_id,
            label=p.name,
            default_enabled=default_enabled(p.unique_id),
            protected=p.unique_id in PROTECTED_PLUGIN_IDS,
            columns=(p.type_name, p.status.name, p.manifest_name or ""),
        )
        for p in plugins
    ]


class PluginSettingsPlugin(SettingsPlugin):
    """Settings page for enabling and disabling plugins at each scope."""

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "plugins"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Plugins"

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
        return 100

    def sections(self) -> Sequence[Section]:
        """The one Plugins section: the catalog of discovered plugins."""
        return [
            Section(
                "Plugins",
                [
                    SettingField(
                        PLUGINS_KEY,
                        Catalog(items=_discovered_plugins, columns=("Type", "Status", "Manifest")),
                        label="Plugins",
                        restart_required=True,
                    )
                ],
                description=(
                    "Choose which plugins load. Changes take effect the next time "
                    "Lightfall starts."
                ),
            )
        ]
