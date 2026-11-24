"""Plan plugin type for Bluesky plans.

PlanPlugin is the plugin type for Bluesky plans. Plugins implementing
this interface provide plan functions that can be discovered, configured,
and executed through the NCS UI.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any, ClassVar

from lightfall.plugins.types import PluginType

if TYPE_CHECKING:
    from lightfall.acquire.plans.registry import PlanInfo


class PlanPlugin(PluginType):
    """Abstract base for Bluesky plan plugins.

    Plan plugins provide custom Bluesky plans that can be discovered
    and registered automatically. Each plugin wraps a plan function
    with metadata for UI generation and introspection.

    Class Attributes:
        type_name: "plan" - identifies this as a plan plugin.
        is_singleton: True - plans are typically singletons.

    Example implementation::

        class MyScanPlan(PlanPlugin):
            @property
            def name(self) -> str:
                return "my_scan"

            @property
            def category(self) -> str:
                return "custom"

            @property
            def icon(self) -> tuple[str, str]:
                return ("#3F51B5", "M")  # indigo circle, white "M"

            def get_plan_function(self) -> Callable:
                return self._my_scan_impl

            def _my_scan_impl(self, detectors, motor, start, stop, num):
                '''My custom scan plan.

                Args:
                    detectors: Detectors to read.
                    motor: Motor to scan.
                    start: Start position.
                    stop: Stop position.
                    num: Number of points.
                '''
                import bluesky.plans as bp
                yield from bp.scan(detectors, motor, start, stop, num)

    The plugin can then be registered via a manifest::

        manifest = PluginManifest(
            name="my-beamline-plans",
            plugins=[
                PluginEntry("plan", "my_scan", "my_beamline.plans:MyScanPlan"),
            ]
        )
    """

    type_name: ClassVar[str] = "plan"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        """Human-readable description of this plan plugin."""
        return "Bluesky plan plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Plan name used for registration and lookup.

        This should be unique within the plan type and is used to
        identify the plan in the registry and UI.
        """
        ...

    @property
    def category(self) -> str:
        """Plan category for grouping (e.g., 'scan', 'alignment').

        Override this to categorize your plan. Categories are used
        in the UI to group related plans.

        Returns:
            Category name. Defaults to "general".
        """
        return "general"

    @property
    def display_name(self) -> str | None:
        """Human-readable name shown in the plans panel.

        Returns:
            Display name, or None to derive one from ``name``
            (``"my_scan"`` -> ``"My Scan"``).
        """
        return None

    @property
    def icon(self) -> tuple[str, str] | None:
        """Plans-panel icon as a ``(color, letter)`` tuple.

        The icon is drawn as a filled circle of ``color`` (any string
        ``QColor`` accepts, e.g. ``"#3F51B5"``) with ``letter`` in white.
        Keep ``letter`` to one or two characters.

        Returns:
            Icon spec, or None to use the category default from
            ``PLAN_CATEGORY_ICONS`` (gray "G" for unknown categories).
        """
        return None

    @property
    def plan_description(self) -> str:
        """Plan description for UI display.

        Override this to provide a custom description. An override that
        returns a non-empty string replaces the description shown in the
        plans panel; otherwise the first paragraph of the plan function's
        docstring is used. By default, returns the docstring of the plan
        function.

        Returns:
            Description text.
        """
        func = self.get_plan_function()
        return func.__doc__ or ""

    @abstractmethod
    def get_plan_function(self) -> Callable[..., Generator[Any, Any, Any]]:
        """Get the plan generator function.

        Returns:
            The Bluesky plan generator function. This function should
            be a generator that yields Bluesky messages.
        """
        ...

    def get_plan_info(self) -> PlanInfo:
        """Get PlanInfo for registration with PlanRegistry.

        This creates a PlanInfo object that can be registered with
        the existing PlanRegistry for use in the Bluesky UI.

        Returns:
            PlanInfo metadata object.
        """
        from lightfall.acquire.plans.registry import PlanInfo

        plan_info = PlanInfo.from_function(
            name=self.name,
            func=self.get_plan_function(),
            category=self.category,
        )
        plan_info.display_name = self.display_name
        # The default plan_description is the raw docstring; from_function
        # already extracted its first paragraph, so only an override applies.
        if type(self).plan_description is not PlanPlugin.plan_description:
            description = self.plan_description
            if description:
                plan_info.description = description
        icon = self.icon
        if icon is not None:
            if (
                not isinstance(icon, (tuple, list))
                or len(icon) != 2
                or not all(isinstance(part, str) and part for part in icon)
            ):
                raise ValueError(
                    f"Plan '{self.name}': icon must be a (color, letter) "
                    f"tuple of non-empty strings, got {icon!r}"
                )
            plan_info.icon = (icon[0], icon[1])
        return plan_info

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with plan information including parameters.
        """
        plan_info = self.get_plan_info()
        return {
            "type": self.type_name,
            "name": self.name,
            "category": self.category,
            "display_name": plan_info.get_display_name(),
            "icon": list(plan_info.get_icon()),
            "description": plan_info.description,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type_name,
                    "required": p.required,
                    "default": str(p.default) if not p.required else None,
                    "description": p.description,
                }
                for p in plan_info.parameters
            ],
            "examples": plan_info.examples,
        }
