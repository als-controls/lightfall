"""Heuristic plugin type for domain-specific visualization scoring.

HeuristicPlugin provides an optional extension mechanism for beamlines
or facilities to adjust visualization scores based on domain knowledge
without modifying the core visualization plugins.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from lucid.plugins.types import PluginType

if TYPE_CHECKING:
    from lucid.visualization.spec import DataCharacteristics


class HeuristicPlugin(PluginType):
    """Plugin type for domain-specific visualization score adjustments.

    HeuristicPlugin allows beamlines to customize visualization selection
    without modifying core visualization plugins. Heuristics are applied
    AFTER all VisualizationPlugins report their base scores.

    Use Cases:
        - Boost specialized visualizations for specific experiment types
        - Suppress generic visualizations when domain-specific ones exist
        - Adjust scores based on beamline-specific metadata

    Priority determines application order (higher = earlier). Multiple
    heuristics can be chained, each modifying the scores from previous.

    Example for an XAS beamline:
        >>> class XASHeuristicPlugin(HeuristicPlugin):
        ...     @property
        ...     def name(self) -> str:
        ...         return "xas_beamline"
        ...
        ...     @property
        ...     def priority(self) -> int:
        ...         return 100  # Run early
        ...
        ...     def should_apply(self, characteristics: DataCharacteristics) -> bool:
        ...         # Only apply for energy scans
        ...         return any("energy" in f.lower()
        ...                    for f in characteristics.dim_fields)
        ...
        ...     def adjust_scores(self, characteristics, scores) -> dict[str, int]:
        ...         # Boost XAS-specific visualization
        ...         if "xas_plot" in scores:
        ...             scores["xas_plot"] += 30
        ...         # Suppress generic scatter for energy data
        ...         if "scatter" in scores:
        ...             scores["scatter"] -= 20
        ...         return scores
    """

    type_name: ClassVar[str] = "heuristic"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        """Human-readable description of this heuristic plugin."""
        return "Visualization score adjustment heuristic"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this heuristic.

        Examples: "xas_beamline", "ptychography_facility", "default_policy"
        """
        ...

    @property
    def priority(self) -> int:
        """Priority for applying this heuristic.

        Higher priority heuristics are applied first. This allows
        facility-wide heuristics to run before beamline-specific ones,
        or vice versa as needed.

        Returns:
            Priority value (default: 50). Higher runs first.
        """
        return 50

    @property
    def display_name(self) -> str:
        """Human-readable name for UI/logging.

        Override to provide a friendlier name than the identifier.
        """
        return self.name.replace("_", " ").title()

    @abstractmethod
    def should_apply(self, characteristics: DataCharacteristics) -> bool:
        """Determine if this heuristic should modify scores.

        Called before adjust_scores() to check if this heuristic is
        relevant for the current data. Return False to skip this
        heuristic entirely (scores pass through unchanged).

        This should be a fast check based on characteristics metadata,
        plan name, or other easily-accessible information.

        Args:
            characteristics: Data characteristics from document stream.

        Returns:
            True if adjust_scores() should be called.
        """
        ...

    @abstractmethod
    def adjust_scores(
        self,
        characteristics: DataCharacteristics,
        scores: dict[str, int],
    ) -> dict[str, int]:
        """Adjust visualization scores based on domain knowledge.

        Receives the current scores (from VisualizationPlugins or previous
        heuristics) and returns modified scores. Can:
        - Boost scores by adding positive values
        - Suppress scores by subtracting values
        - Remove visualizations by setting score to 0
        - Add new visualization scores (though the viz must be registered)

        IMPORTANT: Do not set scores above 100 or below 0 - they will be
        clamped by the SelectionEngine.

        Args:
            characteristics: Data characteristics.
            scores: Dict mapping visualization names to current scores.

        Returns:
            Modified scores dict. May be the same dict or a new one.
        """
        ...

    @classmethod
    def validate_class(cls, plugin_class: type) -> bool:
        """Validate that a class is a valid heuristic plugin.

        Args:
            plugin_class: The class to validate.

        Returns:
            True if the class is a valid HeuristicPlugin.
        """
        if not issubclass(plugin_class, HeuristicPlugin):
            return False

        # Check that required abstract methods are implemented
        required_methods = ["should_apply", "adjust_scores"]
        for method in required_methods:
            if getattr(plugin_class, method) is getattr(HeuristicPlugin, method):
                return False

        return True

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with heuristic plugin information.
        """
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "priority": self.priority,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
