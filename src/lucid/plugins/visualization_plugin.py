"""Visualization plugin type for live data display.

VisualizationPlugin defines the interface for plugins that can display
live Bluesky data. Each plugin knows what types of data it can handle
via the can_handle() method, enabling automatic visualization selection.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from lucid.plugins.types import PluginType

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from lucid.acquire.buffer import MultiStreamBuffer
    from lucid.visualization.base import BaseVisualizationWidget
    from lucid.visualization.spec import DataCharacteristics, VisualizationSpec


class VisualizationPlugin(PluginType):
    """Plugin type for data visualization widgets.

    VisualizationPlugin is the primary mechanism for visualization selection.
    Each plugin implements can_handle() to self-report how well it can
    display given data characteristics. The SelectionEngine queries all
    registered plugins and picks the one(s) with the highest score.

    Scoring Guidelines:
        0: Cannot handle this data at all
        1-39: Can handle but poorly (fallback only)
        40-59: Can handle adequately (baseline)
        60-79: Good match for this data
        80-100: Excellent/optimal match

    Example scores:
        - TABLE always returns ~40 (can show anything)
        - PLOT_1D returns ~80 for 1D scans with scalar dependent vars
        - HEATMAP returns ~85 for 2D rectilinear scalar data
        - IMAGE_STACK returns ~75 for 1D scans with 2D array data

    Example:
        >>> class MyPlotPlugin(VisualizationPlugin):
        ...     @property
        ...     def name(self) -> str:
        ...         return "my_plot"
        ...
        ...     @property
        ...     def display_name(self) -> str:
        ...         return "My Custom Plot"
        ...
        ...     def can_handle(self, characteristics: DataCharacteristics) -> int:
        ...         if characteristics.ndim == 1:
        ...             return 80
        ...         return 0
        ...
        ...     def create_widget(self, spec, buffer, parent=None):
        ...         return MyPlotWidget(spec, buffer, parent)
    """

    type_name: ClassVar[str] = "visualization"
    is_singleton: ClassVar[bool] = True

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this visualization type.

        Examples: "plot_1d", "heatmap", "table", "image_stack"
        """
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for UI display.

        Examples: "1D Plot", "Heatmap", "Data Table", "Image Stack"
        """
        ...

    @property
    def icon(self) -> str:
        """Icon name for UI display (optional).

        Returns:
            Icon name from the application's icon set.
        """
        return "chart-line"

    @property
    def description(self) -> str:
        """Detailed description of this visualization.

        Override to provide more context about when this visualization
        is appropriate and what it displays.
        """
        return f"{self.display_name} visualization"

    @abstractmethod
    def can_handle(self, characteristics: DataCharacteristics) -> int:
        """Determine how well this plugin can handle the given data.

        This is the PRIMARY selection mechanism. Each visualization plugin
        self-selects based on its knowledge of what data it can display well.

        The returned score indicates suitability:
            0: Cannot handle this data
            1-39: Can handle but not ideal (fallback)
            40-59: Baseline handling capability
            60-79: Good match
            80-100: Excellent/optimal match

        This method MUST be fast (<10ms) as it's called for every registered
        visualization plugin during selection.

        Args:
            characteristics: Data characteristics extracted from Bluesky docs.

        Returns:
            Applicability score from 0-100.
        """
        ...

    @abstractmethod
    def create_widget(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> BaseVisualizationWidget:
        """Create the visualization widget.

        Called after this plugin is selected to create the actual widget.
        The widget should connect to the buffer's signals to receive
        live data updates.

        Args:
            spec: Visualization specification with axis assignments, etc.
            buffer: MultiStreamBuffer providing live data.
            parent: Optional Qt parent widget.

        Returns:
            Configured BaseVisualizationWidget subclass.
        """
        ...

    def get_default_spec(
        self, characteristics: DataCharacteristics
    ) -> VisualizationSpec | None:
        """Get a default VisualizationSpec for these characteristics.

        Override to provide sensible defaults for axis assignments, etc.
        The SelectionEngine calls this when creating a widget without
        explicit specification.

        Args:
            characteristics: Data characteristics.

        Returns:
            Default spec, or None to use generic defaults.
        """
        return None

    @classmethod
    def validate_class(cls, plugin_class: type) -> bool:
        """Validate that a class is a valid visualization plugin.

        Args:
            plugin_class: The class to validate.

        Returns:
            True if the class is a valid VisualizationPlugin.
        """
        if not issubclass(plugin_class, VisualizationPlugin):
            return False

        # Check that required abstract methods are implemented
        required_methods = ["can_handle", "create_widget"]
        for method in required_methods:
            if getattr(plugin_class, method) is getattr(VisualizationPlugin, method):
                return False

        return True

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with visualization plugin information.
        """
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "icon": self.icon,
            "description": self.description,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
