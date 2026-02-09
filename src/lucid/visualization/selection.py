"""Visualization selection engine.

SelectionEngine orchestrates the selection of appropriate visualizations
for given data characteristics, with a guaranteed <50ms decision time.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from lucid.visualization.registry import VisualizationRegistry
from lucid.visualization.spec import VisualizationSpec

if TYPE_CHECKING:
    from lucid.plugins.visualization_plugin import VisualizationPlugin
    from lucid.visualization.spec import DataCharacteristics


class SelectionEngine:
    """Engine for selecting visualizations based on data characteristics.

    SelectionEngine queries all registered VisualizationPlugins to get
    their applicability scores, then applies HeuristicPlugins to adjust
    those scores, finally returning the best match(es).

    The engine guarantees <50ms total decision time by:
    1. Keeping can_handle() implementations fast (<10ms each)
    2. Checking timeout between heuristic applications
    3. Short-circuiting if user preferences are explicit

    Selection Flow:
        1. Check for explicit user preference (override)
        2. Query all visualizations via can_handle()
        3. Apply heuristics in priority order via adjust_scores()
        4. Return top results sorted by score

    Example:
        >>> engine = SelectionEngine()
        >>> results = engine.select_visualizations(characteristics)
        >>> for plugin, score in results:
        ...     print(f"{plugin.name}: {score}")
        plot_1d: 80
        table: 40
    """

    TIMEOUT_MS = 50  # Maximum time for selection in milliseconds
    MIN_SCORE = 0  # Minimum score (clamp floor)
    MAX_SCORE = 100  # Maximum score (clamp ceiling)

    def __init__(
        self,
        registry: VisualizationRegistry | None = None,
    ) -> None:
        """Initialize the selection engine.

        Args:
            registry: VisualizationRegistry to use. Defaults to singleton.
        """
        self._registry = registry or VisualizationRegistry.get_instance()
        self._user_preferences: dict[str, str] = {}  # characteristic_key -> viz_name

    def select_visualizations(
        self,
        characteristics: DataCharacteristics,
        max_results: int = 3,
    ) -> list[tuple[VisualizationPlugin, int]]:
        """Select the best visualization(s) for the given data.

        Args:
            characteristics: Data characteristics from Bluesky documents.
            max_results: Maximum number of results to return.

        Returns:
            List of (plugin, score) tuples, sorted by score descending.
            Empty list if no suitable visualization found.
        """
        start = time.monotonic()

        # 1. Check explicit user preference (override)
        pref = self._get_user_preference(characteristics)
        if pref:
            plugin = self._registry.get_visualization(pref)
            if plugin:
                logger.debug("Using user preference: {}", pref)
                return [(plugin, 100)]

        # 2. Query all visualizations for their scores
        scores = self._collect_base_scores(characteristics)

        if not scores:
            logger.warning("No visualizations can handle this data")
            return []

        # 3. Apply heuristics to adjust scores
        scores = self._apply_heuristics(characteristics, scores, start)

        # 4. Clamp scores and filter out zeros
        scores = {
            name: max(self.MIN_SCORE, min(self.MAX_SCORE, score))
            for name, score in scores.items()
            if score > 0
        }

        # 5. Sort by score and return top results
        sorted_scores = sorted(scores.items(), key=lambda x: -x[1])

        results = []
        for name, score in sorted_scores[:max_results]:
            plugin = self._registry.get_visualization(name)
            if plugin:
                results.append((plugin, score))

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug(
            "Selection completed in {:.1f}ms: {}",
            elapsed_ms,
            [(r[0].name, r[1]) for r in results],
        )

        return results

    def _collect_base_scores(
        self, characteristics: DataCharacteristics
    ) -> dict[str, int]:
        """Collect base scores from all visualization plugins.

        Args:
            characteristics: Data characteristics.

        Returns:
            Dict mapping visualization names to scores.
        """
        scores: dict[str, int] = {}

        for plugin in self._registry.get_all_visualizations():
            try:
                score = plugin.can_handle(characteristics)
                if score > 0:
                    scores[plugin.name] = score
            except Exception as e:
                logger.warning(
                    "Error in {}.can_handle(): {}", plugin.name, e
                )

        return scores

    def _apply_heuristics(
        self,
        characteristics: DataCharacteristics,
        scores: dict[str, int],
        start_time: float,
    ) -> dict[str, int]:
        """Apply heuristics to adjust scores.

        Args:
            characteristics: Data characteristics.
            scores: Current visualization scores.
            start_time: Start time for timeout checking.

        Returns:
            Modified scores dict.
        """
        heuristics = self._registry.get_heuristics_by_priority()

        for heuristic in heuristics:
            # Check timeout
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if elapsed_ms > self.TIMEOUT_MS:
                logger.warning(
                    "Selection timeout ({:.1f}ms > {}ms), skipping remaining heuristics",
                    elapsed_ms,
                    self.TIMEOUT_MS,
                )
                break

            try:
                if heuristic.should_apply(characteristics):
                    scores = heuristic.adjust_scores(characteristics, scores)
                    logger.debug(
                        "Applied heuristic '{}': {}",
                        heuristic.name,
                        scores,
                    )
            except Exception as e:
                logger.warning(
                    "Error in heuristic '{}': {}", heuristic.name, e
                )

        return scores

    def _get_user_preference(
        self, characteristics: DataCharacteristics
    ) -> str | None:
        """Get user's explicit visualization preference.

        Args:
            characteristics: Data characteristics.

        Returns:
            Preferred visualization name or None.
        """
        # Check for plan-specific preference
        plan_key = f"plan:{characteristics.plan_name}"
        if plan_key in self._user_preferences:
            return self._user_preferences[plan_key]

        # Check for ndim-based preference
        ndim_key = f"ndim:{characteristics.ndim}"
        if ndim_key in self._user_preferences:
            return self._user_preferences[ndim_key]

        return None

    def set_user_preference(self, key: str, viz_name: str) -> None:
        """Set a user preference for visualization selection.

        Args:
            key: Preference key (e.g., "plan:count", "ndim:2").
            viz_name: Preferred visualization name.
        """
        if self._registry.has_visualization(viz_name):
            self._user_preferences[key] = viz_name
            logger.info("Set visualization preference: {} -> {}", key, viz_name)
        else:
            logger.warning(
                "Cannot set preference: visualization '{}' not registered",
                viz_name,
            )

    def clear_user_preferences(self) -> None:
        """Clear all user preferences."""
        self._user_preferences.clear()
        logger.debug("Cleared visualization preferences")

    def get_spec_for_visualization(
        self,
        plugin: VisualizationPlugin,
        characteristics: DataCharacteristics,
    ) -> VisualizationSpec:
        """Get a VisualizationSpec for the given plugin and data.

        Args:
            plugin: The visualization plugin.
            characteristics: Data characteristics.

        Returns:
            VisualizationSpec configured for this visualization.
        """
        # Try plugin's default spec first
        spec = plugin.get_default_spec(characteristics)
        if spec:
            return spec

        # Fall back to generic spec based on viz type

        viz_name = plugin.name.lower()

        if "plot" in viz_name or "1d" in viz_name:
            return VisualizationSpec.for_plot_1d(characteristics)
        elif "heatmap" in viz_name or "2d" in viz_name:
            return VisualizationSpec.for_heatmap(characteristics)
        elif "image" in viz_name or "stack" in viz_name:
            return VisualizationSpec.for_image_stack(characteristics)
        else:
            return VisualizationSpec.for_table(characteristics)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with engine configuration.
        """
        return {
            "timeout_ms": self.TIMEOUT_MS,
            "user_preferences": dict(self._user_preferences),
            "visualization_count": len(
                self._registry.get_all_visualizations()
            ),
            "heuristic_count": len(self._registry.get_all_heuristics()),
        }
