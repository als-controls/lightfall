"""Controller plugin type for device control widgets.

ControllerPlugin enables plugins to provide device- or pattern-specific
UI widgets for controlling devices. Controllers inspect devices and return
a priority (or None if not applicable), enabling dynamic, device-aware
widget selection.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtWidgets import QWidget

from lucid.plugins.types import PluginType

if TYPE_CHECKING:
    from lucid.ui.models.device_tree import DeviceTreeItem


class ControllerPlugin(PluginType):
    """Abstract base for device controller plugins.

    Controller plugins provide custom device control widgets that can be
    discovered and selected automatically. Each plugin inspects device
    items and returns a priority to indicate how well it can control them.

    The `can_control()` method returns an integer priority or None:
    - None: This plugin cannot control the given items
    - int: Priority value (higher = preferred)

    Suggested priority ranges:
    - 200+: Exact device/prefix match
    - 100-199: Device class match
    - 50-99: Category match
    - 1-49: Generic fallback

    Class Attributes:
        type_name: "controller" - identifies this as a controller plugin.
        is_singleton: True - plugin instance is singleton, creates widgets on demand.

    Example implementation::

        class XYZMotorControllerPlugin(ControllerPlugin):
            @property
            def name(self) -> str:
                return "xyz_motor"

            @property
            def display_name(self) -> str:
                return "XYZ Motor Control"

            def can_control(self, items: list[DeviceTreeItem]) -> int | None:
                if len(items) != 1:
                    return None
                item = items[0]
                if (item.device_info and
                    item.device_info.category == DeviceCategory.MOTOR and
                    item.device_info.prefix.startswith("XYZ:")):
                    return 150  # Higher than default MotorControlWidget
                return None

            def create_widget(self, parent=None) -> QWidget:
                return XYZMotorWidget(parent)

    The plugin can then be registered via a manifest::

        manifest = PluginManifest(
            name="my-beamline-controllers",
            plugins=[
                PluginEntry(
                    "controller",
                    "xyz_motor",
                    "my_beamline.plugins.xyz_motor:XYZMotorControllerPlugin",
                ),
            ]
        )
    """

    type_name: ClassVar[str] = "controller"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        """Human-readable description of this controller plugin."""
        return "Device controller plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Controller name used for registration and lookup.

        This should be unique within the controller type and is used to
        identify the controller in the registry.
        """
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name shown in widget selector.

        Override this to provide a custom display name. By default,
        converts the name to title case (e.g., "xyz_motor" -> "Xyz Motor").

        Returns:
            Display name for UI.
        """
        return self.name.replace("_", " ").title()

    @abstractmethod
    def can_control(self, items: list[DeviceTreeItem]) -> int | None:
        """Check if this controller can handle the given items and return priority.

        This method is called by the ControllerMatcher to determine which
        controllers are applicable for the current selection and their
        relative priority.

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            Priority value (higher = preferred) or None if not applicable.

            Suggested priority ranges:
            - 200+: Exact device/prefix match
            - 100-199: Device class match
            - 50-99: Category match
            - 1-49: Generic fallback
        """
        ...

    @abstractmethod
    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create a new widget instance for controlling devices.

        This is called when the user selects this controller for their
        device selection. The returned widget should support being
        configured with devices via a `set_items()` method (like
        BaseControlWidget).

        Args:
            parent: Parent widget.

        Returns:
            A new widget instance for controlling devices.
        """
        ...

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with controller information.
        """
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
