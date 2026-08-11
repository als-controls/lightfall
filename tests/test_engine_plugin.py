"""Tests for the EnginePlugin system.

Tests the EnginePlugin type, EngineRegistry, and plugin-based engine creation.
"""

import pytest

# qapp is supplied by pytest-qt as a real QApplication (see tests/conftest.py);
# a local QCoreApplication fixture is the wrong type for widget tests and aborts
# the process at teardown (0xC0000005) when they share a run.


@pytest.fixture
def engine_registry():
    """Create a fresh EngineRegistry for testing."""
    from lightfall.acquire.engine.registry import EngineRegistry

    # Reset the singleton
    EngineRegistry.reset()
    registry = EngineRegistry.get_instance()
    yield registry
    # Clean up
    EngineRegistry.reset()


class TestEnginePlugin:
    """Tests for EnginePlugin base class."""

    def test_bluesky_plugin_properties(self) -> None:
        """Test BlueskyEnginePlugin properties."""
        from lightfall.acquire.engine.plugins import BlueskyEnginePlugin

        plugin = BlueskyEnginePlugin()

        assert plugin.name == "bluesky"
        assert plugin.display_name == "Bluesky RunEngine"
        assert plugin.type_name == "engine"
        assert "Bluesky" in plugin.engine_description

    def test_mock_plugin_properties(self) -> None:
        """Test MockEnginePlugin properties."""
        from lightfall.acquire.engine.plugins import MockEnginePlugin

        plugin = MockEnginePlugin()

        assert plugin.name == "mock"
        assert plugin.display_name == "Mock Engine"
        assert plugin.type_name == "engine"
        assert "testing" in plugin.engine_description.lower()

    def test_bluesky_plugin_creates_engine(self, qapp) -> None:
        """Test that BlueskyEnginePlugin creates a BlueskyEngine."""
        pytest.importorskip("bluesky")

        from lightfall.acquire.engine import BlueskyEngine
        from lightfall.acquire.engine.plugins import BlueskyEnginePlugin

        plugin = BlueskyEnginePlugin()
        engine = plugin.create_engine()

        assert isinstance(engine, BlueskyEngine)
        assert engine.name == "bluesky"

    def test_mock_plugin_creates_engine(self, qapp) -> None:
        """Test that MockEnginePlugin creates a MockEngine."""
        from lightfall.acquire.engine import MockEngine
        from lightfall.acquire.engine.plugins import MockEnginePlugin

        plugin = MockEnginePlugin()
        engine = plugin.create_engine()

        assert isinstance(engine, MockEngine)
        assert engine.name == "mock"

    def test_introspection_data(self) -> None:
        """Test that plugins provide introspection data."""
        from lightfall.acquire.engine.plugins import BlueskyEnginePlugin

        plugin = BlueskyEnginePlugin()
        data = plugin.get_introspection_data()

        assert data["type"] == "engine"
        assert data["name"] == "bluesky"
        assert "display_name" in data
        assert "description" in data
        assert "class" in data
        assert "module" in data


class TestEngineRegistry:
    """Tests for EngineRegistry."""

    def test_singleton(self, engine_registry) -> None:
        """Test that EngineRegistry is a singleton."""
        from lightfall.acquire.engine.registry import EngineRegistry

        registry2 = EngineRegistry.get_instance()
        assert registry2 is engine_registry

    def test_register_engine(self, engine_registry) -> None:
        """Test registering an engine plugin."""
        from lightfall.acquire.engine.plugins import MockEnginePlugin

        plugin = MockEnginePlugin()
        engine_registry.register(plugin)

        assert engine_registry.has("mock")
        assert engine_registry.get("mock") is plugin
        assert "mock" in engine_registry.get_names()

    def test_register_multiple_engines(self, engine_registry) -> None:
        """Test registering multiple engine plugins."""
        from lightfall.acquire.engine.plugins import BlueskyEnginePlugin, MockEnginePlugin

        bluesky_plugin = BlueskyEnginePlugin()
        mock_plugin = MockEnginePlugin()

        engine_registry.register(bluesky_plugin)
        engine_registry.register(mock_plugin)

        assert len(engine_registry.get_all()) == 2
        assert engine_registry.get("bluesky") is bluesky_plugin
        assert engine_registry.get("mock") is mock_plugin

    def test_unregister_engine(self, engine_registry) -> None:
        """Test unregistering an engine plugin."""
        from lightfall.acquire.engine.plugins import MockEnginePlugin

        plugin = MockEnginePlugin()
        engine_registry.register(plugin)
        assert engine_registry.has("mock")

        result = engine_registry.unregister("mock")
        assert result is True
        assert not engine_registry.has("mock")

    def test_unregister_nonexistent(self, engine_registry) -> None:
        """Test unregistering an engine that doesn't exist."""
        result = engine_registry.unregister("nonexistent")
        assert result is False

    def test_get_nonexistent(self, engine_registry) -> None:
        """Test getting an engine that doesn't exist."""
        assert engine_registry.get("nonexistent") is None

    def test_default_engine(self, engine_registry) -> None:
        """Test default engine property."""
        assert engine_registry.default_engine == "bluesky"

        engine_registry.default_engine = "mock"
        assert engine_registry.default_engine == "mock"

    def test_introspection_data(self, engine_registry) -> None:
        """Test registry introspection data."""
        from lightfall.acquire.engine.plugins import MockEnginePlugin

        plugin = MockEnginePlugin()
        engine_registry.register(plugin)

        data = engine_registry.get_introspection_data()

        assert "default_engine" in data
        assert "engines" in data
        assert "mock" in data["engines"]


class TestGetEngineWithPlugins:
    """Tests for get_engine() with the plugin system."""

    @pytest.fixture(autouse=True)
    def reset_engine(self):
        """Reset the global engine before each test."""
        from lightfall.acquire.engine import reset_engine
        from lightfall.acquire.engine.registry import EngineRegistry

        reset_engine()
        EngineRegistry.reset()
        yield
        reset_engine()
        EngineRegistry.reset()

    def test_get_engine_with_registered_plugin(self, qapp) -> None:
        """Test get_engine() uses registered plugin."""
        from lightfall.acquire.engine import MockEngine, get_engine
        from lightfall.acquire.engine.plugins import MockEnginePlugin
        from lightfall.acquire.engine.registry import EngineRegistry

        # Register the mock plugin
        registry = EngineRegistry.get_instance()
        registry.register(MockEnginePlugin())

        # Get engine with specific type
        engine = get_engine("mock")

        assert isinstance(engine, MockEngine)

    def test_get_engine_fallback(self, qapp) -> None:
        """Test get_engine() falls back to direct instantiation."""
        from lightfall.acquire.engine import MockEngine, get_engine

        # Don't register any plugins - should fall back to direct instantiation
        engine = get_engine("mock")

        assert isinstance(engine, MockEngine)

    def test_get_engine_unknown_type(self, qapp) -> None:
        """Test get_engine() raises on unknown type."""
        from lightfall.acquire.engine import get_engine

        with pytest.raises(ValueError, match="Unknown engine type"):
            get_engine("unknown_engine_type")


class TestPluginTypeRegistration:
    """Tests for EnginePlugin type registration with PluginLoader."""

    def test_engine_plugin_type_valid(self) -> None:
        """Test that EnginePlugin is a valid plugin type."""
        from lightfall.plugins.engine_plugin import EnginePlugin
        from lightfall.plugins.types import PluginType

        assert issubclass(EnginePlugin, PluginType)
        assert EnginePlugin.type_name == "engine"
        assert EnginePlugin.is_singleton is True

    def test_bluesky_plugin_validates(self) -> None:
        """Test that BlueskyEnginePlugin validates as engine plugin."""
        from lightfall.acquire.engine.plugins import BlueskyEnginePlugin
        from lightfall.plugins.engine_plugin import EnginePlugin

        assert EnginePlugin.validate_class(BlueskyEnginePlugin)
        assert issubclass(BlueskyEnginePlugin, EnginePlugin)

    def test_mock_plugin_validates(self) -> None:
        """Test that MockEnginePlugin validates as engine plugin."""
        from lightfall.acquire.engine.plugins import MockEnginePlugin
        from lightfall.plugins.engine_plugin import EnginePlugin

        assert EnginePlugin.validate_class(MockEnginePlugin)
        assert issubclass(MockEnginePlugin, EnginePlugin)
