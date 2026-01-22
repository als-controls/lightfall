"""Plan plugin type for Bluesky plans.

PlanPlugin is the plugin type for Bluesky plans. Plugins implementing
this interface provide plan functions that can be discovered, configured,
and executed through the NCS UI.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Generator

from ncs.plugins.types import PluginType

if TYPE_CHECKING:
    from ncs.acquire.plans.registry import PlanInfo


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
    description: ClassVar[str] = "Bluesky plan plugin"
    is_singleton: ClassVar[bool] = True

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
    def plan_description(self) -> str:
        """Plan description for UI display.

        Override this to provide a custom description. By default,
        returns the docstring of the plan function.

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
        from ncs.acquire.plans.registry import PlanInfo

        return PlanInfo.from_function(
            name=self.name,
            func=self.get_plan_function(),
            category=self.category,
        )

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
