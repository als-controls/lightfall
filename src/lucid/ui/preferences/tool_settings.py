"""Claude Tools settings plugin for NCS.

This module contains the ClaudeToolsSettingsPlugin that allows users to
view all discovered Claude tool plugins (including skills) and enable/disable
them via checkbox. Enabled/disabled state is persisted in preferences.

This replaces the former skill_settings.py and unifies management of both
mcp_tool and skill plugin types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

    from lucid.plugins.mcp_tool import MCPToolPlugin


class ToolPluginTableModel(QAbstractTableModel):
    """Table model for displaying Claude tool plugins.

    Columns:
        0: Plugin (with checkbox for enabled/disabled)
        1: Type (Tool or Skill)
        2: Category
        3: Description

    The model allows toggling plugins enabled/disabled via the checkbox.
    """

    COLUMNS = ["Plugin", "Type", "Category", "Description"]

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the tool plugin table model."""
        super().__init__(parent)
        self._plugins: list[MCPToolPlugin] = []
        self._enabled_names: set[str] = set()
        self._original_enabled_names: set[str] = set()
        self._has_preference_set = False

    def refresh(self) -> None:
        """Load plugins from MCPToolRegistry.

        Retrieves all tool plugins (including skills) and sorts them
        by type, then category, then by display name.
        """
        self.beginResetModel()
        try:
            from lucid.ui.panels.claude.tool_registry import MCPToolRegistry

            registry = MCPToolRegistry.get_instance()
            self._plugins = registry.get_plugins()
            # Sort by type (skills last), then category, then display name
            self._plugins.sort(
                key=lambda p: (
                    0 if p.type_name == "mcp_tool" else 1,
                    p.category,
                    p.display_name,
                )
            )
        except Exception as e:
            logger.warning("Failed to get MCPToolRegistry: {}", e)
            self._plugins = []
        self.endResetModel()

    def set_enabled_names(
        self,
        enabled_names: set[str] | None,
    ) -> None:
        """Set which plugins are enabled.

        Args:
            enabled_names: Set of plugin names that are enabled, or None if
                no preference has been set (use defaults).
        """
        self.beginResetModel()
        if enabled_names is None:
            # No preference set - use default enabled state from plugins
            self._has_preference_set = False
            self._enabled_names = {
                plugin.name
                for plugin in self._plugins
                if plugin.enabled_by_default
            }
        else:
            self._has_preference_set = True
            self._enabled_names = set(enabled_names)
        self._original_enabled_names = set(self._enabled_names)
        self.endResetModel()

    def get_enabled_names(self) -> set[str]:
        """Get the set of enabled plugin names.

        Returns:
            Set of plugin names that are enabled.
        """
        return set(self._enabled_names)

    def has_changes(self) -> bool:
        """Check if there are unsaved changes.

        Returns:
            True if enabled set has changed from original.
        """
        return self._enabled_names != self._original_enabled_names

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """Return the number of rows."""
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._plugins)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        """Return the number of columns."""
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Return header data for the table."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Return data for a cell."""
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if row < 0 or row >= len(self._plugins):
            return None

        plugin = self._plugins[row]

        if col == 0:  # Plugin column (with checkbox)
            if role == Qt.ItemDataRole.CheckStateRole:
                is_enabled = plugin.name in self._enabled_names
                return Qt.CheckState.Checked if is_enabled else Qt.CheckState.Unchecked
            elif role == Qt.ItemDataRole.DisplayRole:
                return plugin.display_name

        elif role == Qt.ItemDataRole.DisplayRole:
            if col == 1:  # Type
                return "Skill" if plugin.type_name == "skill" else "Tool"
            elif col == 2:  # Category
                return plugin.category.title()
            elif col == 3:  # Description
                return plugin.description

        if role == Qt.ItemDataRole.ToolTipRole:
            # Tooltip shows additional info
            tool_count = len(plugin.create_tools())
            parts = [
                f"Name: {plugin.name}",
                f"Type: {plugin.type_name}",
                f"Priority: {plugin.priority}",
                f"Default: {'enabled' if plugin.enabled_by_default else 'disabled'}",
            ]
            if tool_count > 0:
                parts.append(f"Tools: {tool_count}")
            # Add prompt info for skills
            if hasattr(plugin, "get_system_prompt"):
                prompt = plugin.get_system_prompt()
                if prompt and prompt.strip():
                    parts.append(f"Prompt: {len(prompt)} chars")
            return "\n".join(parts)

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Return flags for a cell."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

        if index.column() == 0:  # Plugin column is checkable
            flags |= Qt.ItemFlag.ItemIsUserCheckable

        return flags

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        """Set data for a cell (handle checkbox toggle)."""
        if not index.isValid():
            return False

        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            row = index.row()
            if 0 <= row < len(self._plugins):
                plugin = self._plugins[row]
                # Qt may pass int or enum, handle both
                check_value = value.value if hasattr(value, 'value') else value
                is_checked = check_value == Qt.CheckState.Checked.value
                if is_checked:
                    # Enable: add to enabled set
                    self._enabled_names.add(plugin.name)
                else:
                    # Disable: remove from enabled set
                    self._enabled_names.discard(plugin.name)
                self.dataChanged.emit(index, index, [role])
                return True

        return False


class ClaudeToolsSettingsPlugin(SettingsPlugin):
    """Settings plugin for managing Claude tool plugins.

    Allows users to view all discovered tool plugins (including skills)
    and enable/disable them. Enabled plugins have their tools available
    to Claude, and enabled skills also contribute system prompts.
    """

    def __init__(self) -> None:
        """Initialize the tool settings plugin."""
        self._widget: QWidget | None = None
        self._table_view: QTableView | None = None
        self._model: ToolPluginTableModel | None = None

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

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the tool plugin settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Description label at top
        description = QLabel(
            "Enable or disable Claude assistant tools and skills. "
            "Tools provide specific capabilities, while skills provide "
            "domain expertise and may include additional tools."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        # Create table model and view
        self._model = ToolPluginTableModel(widget)
        self._table_view = QTableView(widget)
        self._table_view.setModel(self._model)
        self._table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setSortingEnabled(False)  # We sort manually
        self._table_view.verticalHeader().setVisible(False)  # Hide row numbers

        # Configure column widths
        header = self._table_view.horizontalHeader()
        if header:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Plugin
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Type
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Category
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)  # Description

        layout.addWidget(self._table_view, stretch=1)

        # Note label at bottom
        note = QLabel(
            "<i>Hover over a plugin for more details. "
            "Changes take effect immediately for new Claude conversations.</i>"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)

        self._widget = widget
        return widget

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the table with plugins and sets enabled state from preferences.
        Also handles migration from old enabled_skills preference.
        """
        if not self._model:
            return

        # Refresh plugin list from registry
        self._model.refresh()

        # Load enabled plugin names from preferences
        prefs = PreferencesManager.get_instance()

        # Try the new preference key first
        from lucid.ui.panels.claude.tool_registry import MCPToolRegistry
        enabled_list = prefs.get(MCPToolRegistry.ENABLED_PLUGINS_PREF)

        if enabled_list is None:
            # Check for old skills-only preference and migrate
            old_skills_list = prefs.get("enabled_skills")
            if old_skills_list is not None and isinstance(old_skills_list, list):
                # Migrate: old skills + all default-enabled tool plugins
                logger.info("Migrating from enabled_skills to enabled_tool_plugins")
                enabled_set = set(old_skills_list)
                # Add default-enabled mcp_tool plugins
                registry = MCPToolRegistry.get_instance()
                for plugin in registry.get_plugins():
                    if plugin.type_name == "mcp_tool" and plugin.enabled_by_default:
                        enabled_set.add(plugin.name)
                self._model.set_enabled_names(enabled_set)
            else:
                # No preference set - use defaults
                self._model.set_enabled_names(None)
        elif isinstance(enabled_list, list):
            self._model.set_enabled_names(set(enabled_list))
        else:
            self._model.set_enabled_names(set())

        logger.debug(
            "Loaded tool settings: {} plugins, {} enabled",
            self._model.rowCount(),
            len(self._model.get_enabled_names()),
        )

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Saves the enabled plugins list and invalidates the registry cache.
        """
        if not self._model:
            return

        # Get current enabled names
        enabled_names = self._model.get_enabled_names()

        # Save to preferences using the new key
        from lucid.ui.panels.claude.tool_registry import MCPToolRegistry
        prefs = PreferencesManager.get_instance()
        prefs.set(MCPToolRegistry.ENABLED_PLUGINS_PREF, list(enabled_names))

        logger.debug(
            "Saved tool settings: {} enabled",
            len(enabled_names),
        )

        # Invalidate MCPToolRegistry cache so changes take effect
        try:
            registry = MCPToolRegistry.get_instance()
            registry.invalidate_cache()
            logger.debug("Invalidated MCP tool registry cache")
        except Exception as e:
            logger.warning("Failed to invalidate tool registry cache: {}", e)

        # Also invalidate SkillRegistry cache for skills
        try:
            from lucid.ui.panels.claude.skill_registry import SkillRegistry

            skill_registry = SkillRegistry.get_instance()
            skill_registry.invalidate_cache()
            logger.debug("Invalidated skill registry cache")
        except Exception as e:
            logger.warning("Failed to invalidate skill registry cache: {}", e)

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        # No validation needed - any combination of plugins is allowed
        return []


# Backwards compatibility alias
SkillSettingsPlugin = ClaudeToolsSettingsPlugin
