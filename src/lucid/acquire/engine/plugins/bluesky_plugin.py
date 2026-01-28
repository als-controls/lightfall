"""Bluesky engine plugin.

Provides the BlueskyEngine through the plugin system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lucid.plugins.engine_plugin import EnginePlugin

if TYPE_CHECKING:
    from lucid.acquire.engine.base import BaseEngine


class BlueskyEnginePlugin(EnginePlugin):
    """Plugin for Bluesky RunEngine.

    This plugin provides access to the Bluesky data acquisition framework
    through the NCS engine abstraction layer. It creates a BlueskyEngine
    instance that wraps the Bluesky RunEngine with Qt integration.

    The Bluesky RunEngine provides:
    - Plan-based acquisition workflows
    - Document streaming
    - Pause/resume/abort controls
    - Metadata injection
    """

    @property
    def name(self) -> str:
        """Engine identifier."""
        return "bluesky"

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        return "Bluesky RunEngine"

    @property
    def engine_description(self) -> str:
        """Description of this engine."""
        return "Bluesky data acquisition framework with plan-based workflows"

    def create_engine(self, **kwargs: Any) -> BaseEngine:
        """Create a BlueskyEngine instance.

        Args:
            **kwargs: Arguments passed to BlueskyEngine constructor.

        Returns:
            A new BlueskyEngine instance.
        """
        from lucid.acquire.engine.bluesky import BlueskyEngine

        return BlueskyEngine(**kwargs)
