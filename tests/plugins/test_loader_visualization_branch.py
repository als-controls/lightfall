"""Tests for the `visualization` plugin type: builtin registration + loader branch."""
from __future__ import annotations

import pytest

from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.manifest import PluginEntry, PluginManifest
from lightfall.plugins.types import PluginType
from lightfall.visualization.registry import VisualizationRegistry


class _SampleViz(PluginType):
    """Mirrors a real viz plugin: subclasses PluginType directly, not the base."""

    type_name = "visualization"

    @property
    def name(self) -> str:
        return "sample_viz"


@pytest.fixture(autouse=True)
def reset_viz_registry():
    VisualizationRegistry.reset()
    yield
    VisualizationRegistry.reset()


def test_builtin_plugin_types_include_visualization():
    """The app's builtin plugin-type registration must include 'visualization'.

    Without it, manifest entries with type_name='visualization' are silently
    skipped by the loader, so beamline viz plugins never reach the
    VisualizationRegistry (same bug class as the missing 'plan' type).
    """
    from lightfall.main import _register_builtin_plugin_types
    from lightfall.plugins.visualization_plugin import VisualizationPlugin

    loader = PluginLoader()
    _register_builtin_plugin_types(loader)

    assert loader.get_plugin_type("visualization") is VisualizationPlugin


def test_visualization_entry_registers_with_registry():
    """A manifest entry with type_name='visualization' registers with the registry.

    The sample plugin subclasses PluginType directly (the real-world pattern,
    e.g. StxmMapVizPlugin), proving the registered type's validate_class accepts
    existing viz plugins without forcing them onto a new base class.
    """
    from lightfall.plugins.visualization_plugin import VisualizationPlugin

    manifest = PluginManifest(
        name="test_pkg",
        version="0.0.0",
        description="",
        plugins=[
            PluginEntry(
                type_name="visualization",
                name="sample_viz",
                import_path=f"{__name__}:_SampleViz",
            ),
        ],
    )

    loader = PluginLoader()
    loader.register_plugin_type("visualization", VisualizationPlugin)
    loader.load_manifest(manifest)
    successful, failed = loader.load_all_sync()

    assert (successful, failed) == (1, 0)
    assert VisualizationRegistry.get_instance().has_visualization("sample_viz")
