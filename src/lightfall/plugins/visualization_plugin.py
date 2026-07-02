"""Visualization plugin type for live data visualizations.

VisualizationPlugin is the plugin type for ``BaseVisualization`` widgets that
render run data (e.g. a live STXM map). Plugins implementing this interface are
discovered via a manifest entry with ``type_name="visualization"`` and
registered with the :class:`~lightfall.visualization.registry.VisualizationRegistry`.
"""

from __future__ import annotations

from typing import ClassVar

from lightfall.plugins.types import PluginType


class VisualizationPlugin(PluginType):
    """Base for visualization plugins.

    Provides the named, registerable type that the plugin loader binds to the
    ``visualization`` manifest entries (mirroring ``PlanPlugin`` for ``plan``).

    Backward compatibility: existing viz plugins subclass :class:`PluginType`
    directly and merely set ``type_name = "visualization"`` (they predate this
    base class). :meth:`validate_class` therefore accepts *any* ``PluginType``
    subclass that declares ``type_name == "visualization"`` rather than
    requiring inheritance from this class, so those plugins keep loading
    unchanged.

    Example::

        class MyMapViz(VisualizationPlugin):
            @property
            def name(self) -> str:
                return "my_map"

            def get_viz_class(self):
                return MyMapVisualization
    """

    type_name: ClassVar[str] = "visualization"
    # Singleton so the loader instantiates the plugin and the loader's
    # visualization branch can register the *instance* (it reads ``.name``).
    # Non-singletons are stored as the class, which would register a property
    # descriptor instead of the plugin's name.
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        """Human-readable description of this visualization plugin."""
        return "Visualization plugin"

    @classmethod
    def validate_class(cls, plugin_class: type) -> bool:
        """Accept any PluginType subclass declaring ``type_name='visualization'``.

        This intentionally does not require inheritance from
        ``VisualizationPlugin`` so that plugins which subclass ``PluginType``
        directly (the original pattern) remain valid.

        Args:
            plugin_class: The class to validate.

        Returns:
            True if the class is a visualization plugin.
        """
        return (
            isinstance(plugin_class, type)
            and issubclass(plugin_class, PluginType)
            and getattr(plugin_class, "type_name", None) == "visualization"
        )
