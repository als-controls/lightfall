"""Skill settings plugin for NCS.

This module contains the SkillSettingsPlugin that allows users to
view all discovered Claude skills and enable/disable them via checkbox.
Enabled/disabled state is persisted in preferences.
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

    from lucid.plugins.skill_plugin import SkillPlugin


class SkillTableModel(QAbstractTableModel):
    """Table model for displaying Claude skills.

    Columns:
        0: Skill (with checkbox for enabled/disabled)
        1: Category
        2: Description

    The model allows toggling skills enabled/disabled via the checkbox.
    """

    COLUMNS = ["Skill", "Category", "Description"]

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the skill table model."""
        super().__init__(parent)
        self._skills: list[SkillPlugin] = []
        self._enabled_names: set[str] = set()
        self._original_enabled_names: set[str] = set()
        self._has_preference_set = False

    def refresh(self) -> None:
        """Load skills from SkillRegistry.

        Retrieves all skills and sorts them by category, then by name.
        """
        self.beginResetModel()
        try:
            from lucid.ui.panels.claude.skill_registry import SkillRegistry

            registry = SkillRegistry.get_instance()
            self._skills = registry.get_all_skills()
            # Sort by category, then by display name
            self._skills.sort(key=lambda s: (s.category, s.display_name))
        except Exception as e:
            logger.warning("Failed to get SkillRegistry: {}", e)
            self._skills = []
        self.endResetModel()

    def set_enabled_names(
        self,
        enabled_names: set[str] | None,
    ) -> None:
        """Set which skills are enabled.

        Args:
            enabled_names: Set of skill names that are enabled, or None if
                no preference has been set (use defaults).
        """
        self.beginResetModel()
        if enabled_names is None:
            # No preference set - use default enabled state from plugins
            self._has_preference_set = False
            self._enabled_names = {
                skill.name
                for skill in self._skills
                if skill.enabled_by_default
            }
        else:
            self._has_preference_set = True
            self._enabled_names = set(enabled_names)
        self._original_enabled_names = set(self._enabled_names)
        self.endResetModel()

    def get_enabled_names(self) -> set[str]:
        """Get the set of enabled skill names.

        Returns:
            Set of skill names that are enabled.
        """
        return set(self._enabled_names)

    def has_changes(self) -> bool:
        """Check if there are unsaved changes.

        Returns:
            True if enabled set has changed from original.
        """
        return self._enabled_names != self._original_enabled_names

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return the number of rows."""
        if parent.isValid():
            return 0
        return len(self._skills)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return the number of columns."""
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

        if row < 0 or row >= len(self._skills):
            return None

        skill = self._skills[row]

        if col == 0:  # Skill column (with checkbox)
            if role == Qt.ItemDataRole.CheckStateRole:
                is_enabled = skill.name in self._enabled_names
                return Qt.CheckState.Checked if is_enabled else Qt.CheckState.Unchecked
            elif role == Qt.ItemDataRole.DisplayRole:
                return skill.display_name

        elif role == Qt.ItemDataRole.DisplayRole:
            if col == 1:  # Category
                return skill.category.title()
            elif col == 2:  # Description
                return skill.description

        if role == Qt.ItemDataRole.ToolTipRole:
            # Tooltip shows additional info
            tool_count = len(skill.create_tools())
            parts = [
                f"Name: {skill.name}",
                f"Priority: {skill.priority}",
                f"Default: {'enabled' if skill.enabled_by_default else 'disabled'}",
            ]
            if tool_count > 0:
                parts.append(f"Tools: {tool_count}")
            return "\n".join(parts)

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Return flags for a cell."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

        if index.column() == 0:  # Skill column is checkable
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
            if 0 <= row < len(self._skills):
                skill = self._skills[row]
                # Qt may pass int or enum, handle both
                check_value = value.value if hasattr(value, 'value') else value
                is_checked = check_value == Qt.CheckState.Checked.value
                if is_checked:
                    # Enable: add to enabled set
                    self._enabled_names.add(skill.name)
                else:
                    # Disable: remove from enabled set
                    self._enabled_names.discard(skill.name)
                self.dataChanged.emit(index, index, [role])
                return True

        return False


class SkillSettingsPlugin(SettingsPlugin):
    """Settings plugin for managing Claude skills.

    Allows users to view all discovered skills and enable/disable them.
    Enabled skills have their system prompts and tools added to Claude's
    context.
    """

    def __init__(self) -> None:
        """Initialize the skill settings plugin."""
        self._widget: QWidget | None = None
        self._table_view: QTableView | None = None
        self._model: SkillTableModel | None = None

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "skills"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Claude Skills"

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
            A QWidget containing the skill settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Description label at top
        description = QLabel(
            "Enable or disable Claude assistant skills. Skills provide "
            "domain expertise and specialized tools for the Claude assistant."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        # Create table model and view
        self._model = SkillTableModel(widget)
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
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Skill
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Category
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # Description

        layout.addWidget(self._table_view, stretch=1)

        # Note label at bottom
        note = QLabel(
            "<i>Hover over a skill for more details. "
            "Changes take effect immediately for new Claude conversations.</i>"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)

        self._widget = widget
        return widget

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the table with skills and sets enabled state from preferences.
        """
        if not self._model:
            return

        # Refresh skill list from registry
        self._model.refresh()

        # Load enabled skill names from preferences
        prefs = PreferencesManager.get_instance()
        enabled_list = prefs.get("enabled_skills")

        if enabled_list is None:
            # No preference set - use defaults
            self._model.set_enabled_names(None)
        elif isinstance(enabled_list, list):
            self._model.set_enabled_names(set(enabled_list))
        else:
            self._model.set_enabled_names(set())

        logger.debug(
            "Loaded skill settings: {} skills, {} enabled",
            self._model.rowCount(),
            len(self._model.get_enabled_names()),
        )

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Saves the enabled skills list and invalidates the skill registry cache.
        """
        if not self._model:
            return

        # Get current enabled names
        enabled_names = self._model.get_enabled_names()

        # Save to preferences
        prefs = PreferencesManager.get_instance()
        prefs.set("enabled_skills", list(enabled_names))

        logger.debug(
            "Saved skill settings: {} enabled",
            len(enabled_names),
        )

        # Invalidate skill registry cache so changes take effect
        try:
            from lucid.ui.panels.claude.skill_registry import SkillRegistry

            registry = SkillRegistry.get_instance()
            registry.invalidate_cache()
            logger.debug("Invalidated skill registry cache")
        except Exception as e:
            logger.warning("Failed to invalidate skill registry cache: {}", e)

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        # No validation needed - any combination of skills is allowed
        return []
