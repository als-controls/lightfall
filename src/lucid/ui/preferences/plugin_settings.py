"""Plugin settings plugin for NCS.

This module contains the PluginSettingsPlugin that allows users to
view all discovered plugins and enable/disable them via checkbox.
Disabled plugins are persisted and prevented from loading on next start.
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

from lucid.core.services import ServiceRegistry
from lucid.plugins.registry import PluginRegistry
from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

    from lucid.plugins.info import PluginInfo


class PluginTableModel(QAbstractTableModel):
    """Table model for displaying plugins.

    Columns:
        0: Name (with checkbox for enabled/disabled)
        1: Type
        2: Status
        3: Manifest

    The model allows toggling plugins enabled/disabled via the checkbox
    in the Name column. The disabled state is tracked separately from the
    plugin's actual status.
    """

    COLUMNS = ["Name", "Type", "Status", "Manifest"]

    # Plugins that cannot be disabled (would lock user out)
    PROTECTED_PLUGINS = {"settings:plugins"}

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the plugin table model."""
        super().__init__(parent)
        self._plugins: list[PluginInfo] = []
        self._disabled_ids: set[str] = set()
        self._original_disabled_ids: set[str] = set()

    def refresh(self) -> None:
        """Load plugins from PluginRegistry.

        Retrieves all plugins and sorts them by type, then by name.
        """
        self.beginResetModel()
        try:
            services = ServiceRegistry.get_instance()
            registry = services.get(PluginRegistry)
            if registry is not None:
                self._plugins = registry.get_all()
                # Sort by type, then by name
                self._plugins.sort(key=lambda p: (p.type_name, p.name))
            else:
                logger.warning("PluginRegistry not registered")
                self._plugins = []
        except Exception as e:
            logger.warning("Failed to get PluginRegistry: {}", e)
            self._plugins = []
        self.endResetModel()

    def set_disabled_ids(self, disabled_ids: set[str]) -> None:
        """Set which plugins are disabled.

        Args:
            disabled_ids: Set of unique_id strings for disabled plugins.
        """
        self.beginResetModel()
        self._disabled_ids = set(disabled_ids)
        self._original_disabled_ids = set(disabled_ids)
        self.endResetModel()

    def get_disabled_ids(self) -> set[str]:
        """Get the set of disabled plugin IDs.

        Returns:
            Set of unique_id strings for disabled plugins.
        """
        return set(self._disabled_ids)

    def get_newly_disabled(self) -> set[str]:
        """Get plugins that were newly disabled (for signal emission).

        Returns:
            Set of unique_ids that are now disabled but weren't before.
        """
        return self._disabled_ids - self._original_disabled_ids

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

        if col == 0:  # Name column (with checkbox)
            if role == Qt.ItemDataRole.CheckStateRole:
                is_disabled = plugin.unique_id in self._disabled_ids
                return Qt.CheckState.Unchecked if is_disabled else Qt.CheckState.Checked
            elif role == Qt.ItemDataRole.DisplayRole:
                return plugin.name

        elif role == Qt.ItemDataRole.DisplayRole:
            if col == 1:  # Type
                return plugin.type_name
            elif col == 2:  # Status
                return plugin.status.name
            elif col == 3:  # Manifest
                return plugin.manifest_name or ""

        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 2:  # Status tooltip
                if plugin.error:
                    return f"Error: {plugin.error}"
                return plugin.status.name
            elif col == 3:  # Manifest tooltip
                return plugin.import_path

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Return flags for a cell."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

        if index.column() == 0:  # Enabled column is checkable
            # Don't allow unchecking protected plugins
            row = index.row()
            if 0 <= row < len(self._plugins):
                plugin = self._plugins[row]
                if plugin.unique_id not in self.PROTECTED_PLUGINS:
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
                # Don't allow disabling protected plugins
                if plugin.unique_id in self.PROTECTED_PLUGINS:
                    return False
                # Qt may pass int or enum, handle both
                check_value = value.value if hasattr(value, 'value') else value
                is_checked = check_value == Qt.CheckState.Checked.value
                if is_checked:
                    # Enable: remove from disabled set
                    self._disabled_ids.discard(plugin.unique_id)
                else:
                    # Disable: add to disabled set
                    self._disabled_ids.add(plugin.unique_id)
                self.dataChanged.emit(index, index, [role])
                return True

        return False


class PluginSettingsPlugin(SettingsPlugin):
    """Settings plugin for managing installed plugins.

    Allows users to view all discovered plugins and enable/disable them.
    Disabled plugins are persisted in preferences and will not be loaded
    on the next application start.

    Note:
        Newly disabled plugins are logged. Live unloading requires
        application restart since Qt Signal cannot be used with ABC-based
        plugin types.
    """

    def __init__(self) -> None:
        """Initialize the plugin settings plugin."""
        self._widget: QWidget | None = None
        self._table_view: QTableView | None = None
        self._model: PluginTableModel | None = None
        self._newly_disabled: set[str] = set()

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

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the plugin settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Description label at top
        description = QLabel(
            "Manage installed plugins. Disabled plugins will not be loaded "
            "on the next application start."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        # Create table model and view
        self._model = PluginTableModel(widget)
        self._table_view = QTableView(widget)
        self._table_view.setModel(self._model)
        self._table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setSortingEnabled(False)  # We sort manually by type/name
        self._table_view.verticalHeader().setVisible(False)  # Hide row numbers

        # Configure column widths
        header = self._table_view.horizontalHeader()
        if header:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Name
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Type
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Status
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Manifest

        layout.addWidget(self._table_view, stretch=1)

        # Note label at bottom
        note = QLabel(
            "<i>Note: Changes take effect on next application start unless "
            "the plugin supports live unloading.</i>"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)

        self._widget = widget
        return widget

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the table with plugins and sets disabled state from preferences.
        """
        if not self._model:
            return

        # Refresh plugin list from registry
        self._model.refresh()

        # Load disabled plugin IDs from preferences
        prefs = PreferencesManager.get_instance()
        disabled_list = prefs.get("disabled_plugins", [])

        if isinstance(disabled_list, list):
            disabled_ids = set(disabled_list)
        else:
            disabled_ids = set()

        self._model.set_disabled_ids(disabled_ids)

        logger.debug(
            "Loaded plugin settings: {} plugins, {} disabled",
            self._model.rowCount(),
            len(disabled_ids),
        )

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Saves the disabled plugin list. Newly disabled plugins are logged
        and will not be loaded on next application restart.
        """
        if not self._model:
            return

        # Get newly disabled plugins before saving
        self._newly_disabled = self._model.get_newly_disabled()

        # Get current disabled IDs
        disabled_ids = self._model.get_disabled_ids()

        # Save to preferences
        prefs = PreferencesManager.get_instance()
        prefs.set("disabled_plugins", list(disabled_ids))

        logger.debug(
            "Saved plugin settings: {} disabled, {} newly disabled",
            len(disabled_ids),
            len(self._newly_disabled),
        )

        # Log newly disabled plugins
        for unique_id in self._newly_disabled:
            logger.info("Plugin disabled (restart required): {}", unique_id)

    def get_newly_disabled(self) -> set[str]:
        """Get plugins that were newly disabled in the last save.

        Returns:
            Set of unique_ids that were newly disabled.
        """
        return self._newly_disabled

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        # No validation needed - disabling any plugin is allowed
        return []
