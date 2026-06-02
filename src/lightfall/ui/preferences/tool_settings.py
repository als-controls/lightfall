"""Claude agent settings plugin for NCS.

ClaudeToolsSettingsPlugin lets users enable/disable AgentPlugin instances
discovered by AgentRegistry. The user's choices are persisted as overrides
of each plugin's `enabled_by_default`:

- `disabled_tool_plugins`: default-enabled plugins the user unchecked.
- `forced_enabled_tool_plugins`: default-disabled plugins the user checked.

A plugin not listed in either pref falls through to its `enabled_by_default`
value, so newly-registered plugins are enabled by default without the user
having to revisit settings.
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

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

    from lightfall.plugins.agent_plugin import AgentPlugin


class ToolPluginTableModel(QAbstractTableModel):
    """Table model for displaying registered AgentPlugin instances.

    Columns:
        0: Plugin (with checkbox for enabled/disabled)
        1: Category
        2: Description

    The model allows toggling plugins enabled/disabled via the checkbox.
    """

    COLUMNS = ["Plugin", "Category", "Description"]

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the tool plugin table model."""
        super().__init__(parent)
        self._plugins: list[AgentPlugin] = []
        self._enabled_names: set[str] = set()
        self._original_enabled_names: set[str] = set()

    def refresh(self) -> None:
        """Load plugins from AgentRegistry.

        Retrieves all agent plugins and sorts them by category, then display name.
        """
        self.beginResetModel()
        try:
            from lightfall.ui.panels.claude.agent_registry import AgentRegistry

            registry = AgentRegistry.get_instance()
            self._plugins = registry.get_plugins()
            # Sort by category, then display name
            self._plugins.sort(
                key=lambda p: (
                    p.category,
                    p.display_name,
                )
            )
        except Exception as e:
            logger.warning("Failed to get AgentRegistry: {}", e)
            self._plugins = []
        self.endResetModel()

    def set_overrides(
        self,
        disabled_names: set[str],
        forced_enabled_names: set[str],
    ) -> None:
        """Apply persisted overrides to the per-plugin checkbox state.

        Each plugin's effective state is its `enabled_by_default` value
        minus any overrides: name in `disabled_names` forces it off, name
        in `forced_enabled_names` forces it on.
        """
        self.beginResetModel()
        self._enabled_names = {
            plugin.name
            for plugin in self._plugins
            if (plugin.enabled_by_default and plugin.name not in disabled_names)
            or (not plugin.enabled_by_default and plugin.name in forced_enabled_names)
        }
        self._original_enabled_names = set(self._enabled_names)
        self.endResetModel()

    def get_overrides(self) -> tuple[set[str], set[str]]:
        """Return (disabled, forced_enabled) for the current checkbox state.

        Only plugins whose state diverges from `enabled_by_default` are
        recorded, so the persisted overrides stay minimal.
        """
        disabled: set[str] = set()
        forced_enabled: set[str] = set()
        for plugin in self._plugins:
            checked = plugin.name in self._enabled_names
            if plugin.enabled_by_default and not checked:
                disabled.add(plugin.name)
            elif not plugin.enabled_by_default and checked:
                forced_enabled.add(plugin.name)
        return disabled, forced_enabled

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
            if col == 1:  # Category
                return plugin.category.title()
            elif col == 2:  # Description
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
    """Settings plugin for managing Claude agent plugins.

    Allows users to view all AgentPlugin instances registered with
    AgentRegistry and enable/disable them. Enabled plugins contribute
    their tools (per-plugin MCP server) and skill prompt (SKILL.md
    materialized into the per-session SDK plugin dir).
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
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Category
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # Description

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
        """Populate the table from the registry and apply persisted overrides."""
        if not self._model:
            return

        self._model.refresh()

        from lightfall.ui.panels.claude.agent_registry import AgentRegistry
        AgentRegistry.get_instance()._migrate_legacy_pref_if_needed()

        from lightfall.ui.panels.claude.agent_registry import (
            DISABLED_PLUGINS_PREF,
            FORCED_ENABLED_PLUGINS_PREF,
        )
        prefs = PreferencesManager.get_instance()
        disabled = prefs.get(DISABLED_PLUGINS_PREF)
        forced = prefs.get(FORCED_ENABLED_PLUGINS_PREF)
        self._model.set_overrides(
            set(disabled) if isinstance(disabled, list) else set(),
            set(forced) if isinstance(forced, list) else set(),
        )

        d, f = self._model.get_overrides()
        logger.debug(
            "Loaded tool settings: {} plugins, {} disabled, {} forced-enabled",
            self._model.rowCount(), len(d), len(f),
        )

    def save_settings(self) -> None:
        """Persist the user's overrides relative to each plugin's default."""
        if not self._model:
            return

        disabled, forced_enabled = self._model.get_overrides()

        from lightfall.ui.panels.claude.agent_registry import (
            DISABLED_PLUGINS_PREF,
            FORCED_ENABLED_PLUGINS_PREF,
        )
        prefs = PreferencesManager.get_instance()
        prefs.set(DISABLED_PLUGINS_PREF, sorted(disabled))
        prefs.set(FORCED_ENABLED_PLUGINS_PREF, sorted(forced_enabled))

        logger.debug(
            "Saved tool settings: {} disabled, {} forced-enabled",
            len(disabled), len(forced_enabled),
        )

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        # No validation needed - any combination of plugins is allowed
        return []


# Backwards compatibility alias
SkillSettingsPlugin = ClaudeToolsSettingsPlugin
