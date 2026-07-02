"""Monitor settings plugin for NCS.

MonitorSettingsPlugin lets users enable/disable MonitorPlugin instances
discovered by MonitorRegistry, toggle the LLM advisor, and set the tick
interval. Mirrors ClaudeToolsSettingsPlugin (tool_settings.py:241).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from lightfall.monitor.registry import (
    DISABLED_MONITORS_PREF,
    FORCED_ENABLED_MONITORS_PREF,
    MonitorRegistry,
)
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.utils.logging import logger

ADVISOR_ENABLED_PREF = "monitor_advisor_enabled"   # bool, default False
TICK_INTERVAL_PREF = "monitor_tick_interval"        # int seconds, default 60


class MonitorPluginTableModel(QAbstractTableModel):
    """Table model for displaying registered MonitorPlugin instances.

    Columns:
        0: Plugin (with checkbox for enabled/disabled)
        1: Category
        2: Description
    """

    COLUMNS = ["Plugin", "Category", "Description"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._plugins = []
        self._enabled_names: set[str] = set()
        self._original_enabled_names: set[str] = set()

    def refresh(self) -> None:
        self.beginResetModel()
        try:
            registry = MonitorRegistry.get_instance()
            self._plugins = registry.get_plugins()
            self._plugins.sort(key=lambda p: (p.category, p.display_name))
        except Exception as e:
            logger.warning("Failed to get MonitorRegistry: {}", e)
            self._plugins = []
        self.endResetModel()

    def set_overrides(
        self,
        disabled_names: set[str],
        forced_enabled_names: set[str],
    ) -> None:
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
        return self._enabled_names != self._original_enabled_names

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._plugins)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
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
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
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
            parts = [
                f"Name: {plugin.name}",
                f"Type: {plugin.type_name}",
                f"Priority: {plugin.priority}",
                f"Default: {'enabled' if plugin.enabled_by_default else 'disabled'}",
            ]
            return "\n".join(parts)

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
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
        if not index.isValid():
            return False

        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            row = index.row()
            if 0 <= row < len(self._plugins):
                plugin = self._plugins[row]
                check_value = value.value if hasattr(value, 'value') else value
                is_checked = check_value == Qt.CheckState.Checked.value
                if is_checked:
                    self._enabled_names.add(plugin.name)
                else:
                    self._enabled_names.discard(plugin.name)
                self.dataChanged.emit(index, index, [role])
                return True

        return False


class MonitorSettingsPlugin(SettingsPlugin):
    """Monitor settings: per-feed enable table + advisor switch + tick interval.
    Mirrors ClaudeToolsSettingsPlugin (tool_settings.py:241)."""

    def __init__(self) -> None:
        self._widget = None
        self._table_view = None
        self._model = None
        self._advisor_check = None
        self._interval_spin = None

    @property
    def name(self) -> str: return "monitor"
    @property
    def display_name(self) -> str: return "Monitor"
    @property
    def category(self) -> str: return "advanced"
    @property
    def priority(self) -> int: return 95

    def create_widget(self, parent=None):
        from PySide6.QtWidgets import (
            QCheckBox,
            QFormLayout,
            QHeaderView,
            QLabel,
            QSpinBox,
            QTableView,
            QVBoxLayout,
            QWidget,
        )
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(
            "Enable or disable monitor feeds, the advisor, and the tick interval."))
        form = QFormLayout()
        self._advisor_check = QCheckBox("Enable monitor advisor (LLM)")
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 86400)
        self._interval_spin.setSuffix(" s")
        form.addRow("Advisor:", self._advisor_check)
        form.addRow("Tick interval:", self._interval_spin)
        layout.addLayout(form)
        self._model = MonitorPluginTableModel(widget)
        self._table_view = QTableView(widget)
        self._table_view.setModel(self._model)
        self._table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.verticalHeader().setVisible(False)
        header = self._table_view.horizontalHeader()
        if header:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table_view, stretch=1)
        self._widget = widget
        return widget

    def load_settings(self) -> None:
        if not self._model:
            return
        self._model.refresh()
        prefs = PreferencesManager.get_instance()
        disabled = prefs.get(DISABLED_MONITORS_PREF)
        forced = prefs.get(FORCED_ENABLED_MONITORS_PREF)
        self._model.set_overrides(
            set(disabled) if isinstance(disabled, list) else set(),
            set(forced) if isinstance(forced, list) else set(),
        )
        self._advisor_check.setChecked(bool(prefs.get(ADVISOR_ENABLED_PREF, False)))
        self._interval_spin.setValue(int(prefs.get(TICK_INTERVAL_PREF, 60)))

    def save_settings(self) -> None:
        if not self._model:
            return
        disabled, forced_enabled = self._model.get_overrides()
        prefs = PreferencesManager.get_instance()
        prefs.set(DISABLED_MONITORS_PREF, sorted(disabled))
        prefs.set(FORCED_ENABLED_MONITORS_PREF, sorted(forced_enabled))
        prefs.set(ADVISOR_ENABLED_PREF, self._advisor_check.isChecked())
        prefs.set(TICK_INTERVAL_PREF, self._interval_spin.value())

    def validate(self) -> list[str]:
        return []
