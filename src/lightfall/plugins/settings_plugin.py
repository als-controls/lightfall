"""Settings plugin type for preferences pages.

SettingsPlugin is the plugin type for preferences/settings pages. Plugins
implementing this interface provide settings widgets that appear in the
Preferences dialog.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from lucid.plugins.types import PluginType

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QWidget


class SettingsPlugin(PluginType):
    """Abstract base for settings/preferences plugins.

    Settings plugins provide preference pages that can be discovered
    and displayed in the Preferences dialog. Each plugin creates a widget
    containing its settings controls.

    Class Attributes:
        type_name: "settings" - identifies this as a settings plugin.
        is_singleton: True - settings plugins are singletons.

    Lifecycle:
        1. Plugin is instantiated on load
        2. on_loaded() is called (for preload plugins, applies settings immediately)
        3. When Preferences dialog opens:
           - create_widget() is called once and cached
           - load_settings() populates widget with current values
        4. As user interacts:
           - apply_preview() is called for live preview
        5. On OK/Apply:
           - validate() checks values
           - save_settings() persists values
        6. On Cancel:
           - revert_preview() undoes any preview changes

    Example implementation::

        class MySettingsPlugin(SettingsPlugin):
            @property
            def name(self) -> str:
                return "my_settings"

            @property
            def display_name(self) -> str:
                return "My Settings"

            def create_widget(self, parent=None) -> QWidget:
                widget = QWidget(parent)
                # Create controls...
                return widget

            def load_settings(self) -> None:
                prefs = PreferencesManager.get_instance()
                self._my_control.setValue(prefs.get("my_key"))

            def save_settings(self) -> None:
                prefs = PreferencesManager.get_instance()
                prefs.set("my_key", self._my_control.value())
    """

    type_name: ClassVar[str] = "settings"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        """Human-readable description of this settings plugin."""
        return "Settings/Preferences plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this settings plugin.

        This should be unique within the settings type and is used to
        identify the plugin in the registry.
        """
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name shown in preferences sidebar.

        Override this to provide a custom display name. By default,
        converts the name to title case.

        Returns:
            Display name for the sidebar.
        """
        return self.name.replace("_", " ").title()

    @property
    def icon(self) -> QIcon | None:
        """Optional icon for the preferences sidebar.

        Override this to provide an icon. Returns None by default.

        Returns:
            QIcon or None.
        """
        return None

    @property
    def category(self) -> str:
        """Category for grouping settings in the sidebar.

        Override this to group related settings together.
        Common categories: "general", "appearance", "advanced", "plugins".

        Returns:
            Category name. Defaults to "general".
        """
        return "general"

    @property
    def priority(self) -> int:
        """Sort order within category (lower = higher in list).

        Override this to control ordering. Lower values appear first.

        Returns:
            Priority value. Defaults to 100.
        """
        return 100

    @abstractmethod
    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        This is called once when the Preferences dialog opens. The returned
        widget is cached and reused for subsequent opens.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the settings controls.
        """
        ...

    @abstractmethod
    def load_settings(self) -> None:
        """Load current settings into the widget.

        Called when the Preferences dialog opens, after create_widget().
        Should populate the widget controls with current values from
        PreferencesManager.
        """
        ...

    @abstractmethod
    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Called when the user clicks OK or Apply, after validate() passes.
        Should write values from the widget controls to PreferencesManager.
        """
        ...

    def validate(self) -> list[str]:
        """Validate current widget values.

        Called before save_settings(). Return a list of error messages
        for any validation failures. If the list is non-empty, save is
        blocked and errors are displayed.

        Returns:
            List of error messages, or empty list if valid.
        """
        return []

    def apply_preview(self) -> None:
        """Apply settings temporarily for live preview.

        Called when the user changes settings in the dialog, allowing
        immediate visual feedback (e.g., theme changes). Preview changes
        should be reversible via revert_preview().

        Override this for settings that benefit from live preview.
        """
        pass

    def revert_preview(self) -> None:
        """Revert any preview changes.

        Called when the user cancels the Preferences dialog. Should undo
        any changes made by apply_preview() and restore original values.

        Override this if you implement apply_preview().
        """
        pass

    def on_loaded(self) -> None:
        """Called when plugin is loaded.

        For preload plugins (preload=True), this is called before the main
        window is created. Use this to apply settings immediately, such as
        setting the theme before any windows appear.

        Override this if your settings need to be applied at startup.
        """
        pass

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with settings plugin information.
        """
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "category": self.category,
            "priority": self.priority,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
