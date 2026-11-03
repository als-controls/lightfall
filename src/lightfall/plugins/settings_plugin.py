"""Settings plugin type for preferences pages.

SettingsPlugin is the plugin type for preferences/settings pages. Plugins
implementing this interface provide settings widgets that appear in the
Preferences dialog.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from lightfall.plugins.types import PluginType

if TYPE_CHECKING:
    from lightfall_settings import SettingSpec
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QWidget

    from lightfall.ui.preferences.form.schema import FormValues, Section


class SettingsPlugin(PluginType):
    """Abstract base for settings/preferences plugins.

    Settings plugins provide preference pages that can be discovered
    and displayed in the Preferences dialog. A page is one of two kinds:

    * Form page: override ``sections()`` (and ``specs()`` for any keys not
      already declared). The dialog generates the widget from the schema, edits
      it at the scope chosen in its header and saves only the changed values.
      Optional hooks: ``validate_values``, ``after_save``, ``restart_reason``,
      ``preview`` / ``revert_preview`` and ``console_keys``.
    * Custom page: set ``scope_aware = False`` and override ``create_widget``.
      The page builds its own widget and applies changes immediately; the
      dialog never loads, validates or saves it.

    A plugin that is neither is shown as an "outdated plugin" placeholder.

    Class Attributes:
        type_name: "settings" - identifies this as a settings plugin.
        is_singleton: True - settings plugins are singletons.
        scope_aware: False for a custom page.

    Lifecycle:
        1. Plugin is instantiated on load; its specs and console keys are registered
        2. on_loaded() is called (for preload plugins, applies settings immediately)
        3. When the Preferences dialog opens, a form page's widget is generated
           from sections(); a custom page's create_widget() is called
        4. As the user edits a form page, preview(values) is called
        5. On OK/Apply, form pages are validated (field validators and
           validate_values()) and their changed values written, then
           after_save() and restart_reason() are called
        6. On Cancel, revert_preview() undoes any preview

    Example form page::

        class MySettingsPlugin(SettingsPlugin):
            @property
            def name(self) -> str:
                return "my_settings"

            @property
            def display_name(self) -> str:
                return "My Settings"

            def sections(self):
                return [Section("General", [SettingField("my_key", Toggle(), label="My key")])]
    """

    type_name: ClassVar[str] = "settings"
    is_singleton: ClassVar[bool] = True
    scope_aware: ClassVar[bool] = True

    def __init__(self) -> None:
        # Many existing pages override __init__ without calling super(); the loader
        # also registers specs after instantiation (registration is idempotent).
        self.register_specs()

    def register_specs(self) -> None:
        """Declare this page's specs in the shared client registry, and its console keys."""
        from lightfall.scopes.keys import register_client_specs, register_console_keys

        register_client_specs(self.specs())
        register_console_keys(self.console_keys())

    def sections(self) -> Sequence[Section]:
        """Schema sections for a form page; empty for custom pages."""
        return ()

    def specs(self) -> Sequence[SettingSpec]:
        """Setting specs this page declares (keys not already declared elsewhere).

        Called at registration time, so it must not depend on instance state set
        after ``super().__init__()``.
        """
        return ()

    def console_keys(self) -> Sequence[str]:
        """Keys that belong to this console only (its devices, paths, daemons).

        They are shown and saved only at "This console" and never written to a
        server scope, like the core's local-only keys. Called at registration time.
        """
        return ()

    def validate_values(self, values: FormValues) -> list[str]:
        """Cross-field validation of a form page; return error messages."""
        return []

    def after_save(self, scope: str, changed: Mapping[str, Any]) -> None:
        """Called after a form page saved ``changed`` values at ``scope``."""

    def restart_reason(self, scope: str, changed: Mapping[str, Any]) -> str:
        """Why the values just saved at ``scope`` need a restart; "" if they don't.

        Called after ``after_save`` with the same ``changed`` (companion keys included).
        A non-empty reason makes the dialog offer one restart and lists it there.
        """
        return ""

    def preview(self, values: FormValues) -> None:
        """Live preview of a form page's pending ``values`` (at ``values.scope``).

        Called after every edit on this page's form. Undo it in ``revert_preview()``,
        which the dialog calls on Cancel. Default: no preview.
        """

    def is_form_page(self) -> bool:
        """True if this page is generated from ``sections()``."""
        return bool(self.sections())

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
        raise NotImplementedError

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

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create a custom page's widget (``scope_aware = False`` only).

        Called when the Preferences dialog opens. The page applies its
        changes immediately; form pages don't override this.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the settings controls.
        """
        raise NotImplementedError

    def revert_preview(self) -> None:
        """Revert any preview changes.

        Called when the user cancels the Preferences dialog. Should undo
        any changes made by preview() and restore the saved values.
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
