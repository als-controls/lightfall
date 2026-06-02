"""Preferences dialog for NCS.

Provides a plugin-driven dialog for viewing and editing user preferences.
Settings plugins are discovered from the PluginRegistry and rendered
dynamically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from lucid.plugins.settings_plugin import SettingsPlugin


class PreferencesDialog(QDialog):
    """Plugin-driven dialog for editing user preferences.

    Discovers all registered SettingsPlugin instances and renders them
    as pages in a sidebar-navigated dialog. Each plugin provides its own
    widget and handles loading/saving its settings.

    Example:
        >>> dialog = PreferencesDialog(parent)
        >>> if dialog.exec() == QDialog.DialogCode.Accepted:
        ...     # Preferences were applied

    The dialog supports:
    - Live preview via apply_preview()
    - Validation before save via validate()
    - Revert on cancel via revert_preview()
    """

    def __init__(
        self, parent: QWidget | None = None, initial_page: str | None = None
    ) -> None:
        """Initialize the preferences dialog.

        Args:
            parent: Parent widget.
            initial_page: Optional plugin name to navigate to initially.
        """
        super().__init__(parent)
        self._plugins: dict[str, SettingsPlugin] = {}
        self._plugin_widgets: dict[str, QWidget] = {}
        self._initial_page = initial_page

        self._setup_ui()
        self._load_plugins()

        # Navigate to initial page if specified
        if initial_page:
            self.select_page(initial_page)

    def _setup_ui(self) -> None:
        """Setup the dialog UI with sidebar navigation."""
        self.setWindowTitle("Preferences")
        self.setMinimumSize(600, 400)

        layout = QHBoxLayout(self)
        layout.setSpacing(16)

        # Left sidebar for navigation
        self._sidebar = QListWidget()
        self._sidebar.setMaximumWidth(150)
        self._sidebar.setMinimumWidth(120)
        self._sidebar.currentRowChanged.connect(self._on_page_changed)
        layout.addWidget(self._sidebar)

        # Right content area
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Stack for plugin widgets
        self._stack = QStackedWidget()
        right_layout.addWidget(self._stack, 1)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        apply_button = button_box.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button:
            apply_button.clicked.connect(self._apply)

        right_layout.addWidget(button_box)
        layout.addLayout(right_layout, 1)

    def _load_plugins(self) -> None:
        """Load all settings plugins and create their widgets."""
        from lucid.core.services import ServiceRegistry
        from lucid.plugins.registry import PluginRegistry

        try:
            services = ServiceRegistry.get_instance()
            registry = services.get(PluginRegistry)
        except Exception:
            # PluginRegistry may not be initialized yet
            logger.warning("PluginRegistry not available, preferences dialog will be empty")
            return

        if registry is None:
            logger.warning("PluginRegistry not registered, preferences dialog will be empty")
            return

        plugin_infos = registry.get_ready_by_type("settings")

        if not plugin_infos:
            logger.debug("No settings plugins found")
            return

        # Sort by category, then priority
        plugin_infos.sort(key=lambda p: (p.instance.category, p.instance.priority))

        for info in plugin_infos:
            plugin = info.instance
            self._plugins[plugin.name] = plugin

            # Create and cache widget
            try:
                widget = plugin.create_widget(self)
                self._plugin_widgets[plugin.name] = widget
                plugin.load_settings()

                # Add to UI
                item = QListWidgetItem(plugin.display_name)
                if plugin.icon:
                    item.setIcon(plugin.icon)
                self._sidebar.addItem(item)
                self._stack.addWidget(widget)

                logger.debug("Loaded settings plugin: {}", plugin.name)
            except Exception as e:
                logger.error("Failed to load settings plugin '{}': {}", plugin.name, e)

        # Select first item
        if self._sidebar.count() > 0:
            self._sidebar.setCurrentRow(0)

    def _on_page_changed(self, row: int) -> None:
        """Handle sidebar selection change.

        Args:
            row: The selected row index.
        """
        self._stack.setCurrentIndex(row)

    def select_page(self, plugin_name: str) -> bool:
        """Select a settings page by plugin name.

        Args:
            plugin_name: The name of the plugin to navigate to.

        Returns:
            True if the page was found and selected.
        """
        for i, name in enumerate(self._plugins.keys()):
            if name == plugin_name:
                self._sidebar.setCurrentRow(i)
                return True
        return False

    def _apply(self) -> bool:
        """Apply settings without closing.

        Returns:
            True if settings were applied successfully.
        """
        # Validate all plugins
        errors: list[str] = []
        for _name, plugin in self._plugins.items():
            try:
                plugin_errors = plugin.validate()
                if plugin_errors:
                    for err in plugin_errors:
                        errors.append(f"{plugin.display_name}: {err}")
            except Exception as e:
                errors.append(f"{plugin.display_name}: Validation error - {e}")

        if errors:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please fix the following errors:\n\n" + "\n".join(errors),
            )
            return False

        # Save all plugins
        for plugin in self._plugins.values():
            try:
                plugin.save_settings()
                logger.debug("Saved settings for plugin: {}", plugin.name)
            except Exception as e:
                logger.error("Failed to save settings for '{}': {}", plugin.name, e)
                QMessageBox.critical(
                    self,
                    "Error",
                    f"Failed to save {plugin.display_name} settings: {e}",
                )
                return False

        return True

    def accept(self) -> None:
        """Validate, save preferences, and close dialog."""
        if self._apply():
            super().accept()

    def reject(self) -> None:
        """Revert any preview changes and close dialog."""
        for plugin in self._plugins.values():
            try:
                plugin.revert_preview()
            except Exception as e:
                logger.error("Error reverting preview for '{}': {}", plugin.name, e)
        super().reject()
