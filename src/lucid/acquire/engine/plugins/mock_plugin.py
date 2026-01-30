"""Mock engine plugin for testing.

Provides the MockEngine through the plugin system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lucid.plugins.engine_plugin import EnginePlugin

if TYPE_CHECKING:
    from lucid.acquire.engine.base import BaseEngine


class MockEnginePlugin(EnginePlugin):
    """Plugin for MockEngine (testing/development).

    This plugin provides a simulated engine for testing and development
    purposes. The MockEngine executes procedures synchronously and
    emits mock documents without requiring Bluesky or any hardware.

    Use cases:
    - Unit testing without Bluesky dependencies
    - UI development without hardware
    - Demonstration and tutorials
    """

    @property
    def name(self) -> str:
        """Engine identifier."""
        return "mock"

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        return "Mock Engine"

    @property
    def engine_description(self) -> str:
        """Description of this engine."""
        return "Simulated engine for testing and development"

    def create_engine(self, **kwargs: Any) -> BaseEngine:
        """Create a MockEngine instance.

        Args:
            **kwargs: Arguments (currently unused by MockEngine).

        Returns:
            A new MockEngine instance.
        """
        from lucid.acquire.engine.mock import MockEngine

        return MockEngine()
