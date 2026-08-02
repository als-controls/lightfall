"""Tests for the `agent` plugin type loader branch."""
from __future__ import annotations

import pytest

from lightfall.plugins.tool_plugin import ToolPlugin
from lightfall.plugins.manifest import PluginEntry, PluginManifest
from lightfall.ui.panels.claude.tool_registry import ToolRegistry


class _SampleAgent(ToolPlugin):
    @property
    def name(self): return "sample_agent"
    @property
    def description(self): return "sample"


class _NotAnAgent:  # plain object, not a PluginType
    pass


@pytest.fixture(autouse=True)
def reset_registry():
    ToolRegistry.reset_instance()
    yield
    ToolRegistry.reset_instance()


def _make_loader_with_agent_type():
    from lightfall.plugins.loader import PluginLoader

    loader = PluginLoader()
    loader.register_plugin_type("tool", ToolPlugin)
    return loader


def test_agent_entry_registers_with_tool_registry():
    """Manifest entry with type_name='agent' triggers ToolRegistry.register."""
    manifest = PluginManifest(
        name="test_pkg",
        version="0.0.0",
        description="",
        plugins=[
            PluginEntry(
                type_name="tool",
                name="sample_agent",
                import_path=f"{__name__}:_SampleAgent",
            ),
        ],
    )

    loader = _make_loader_with_agent_type()
    loader.load_manifest(manifest)
    successful, failed = loader.load_all_sync()

    assert successful == 1
    assert failed == 0
    registered = ToolRegistry.get_instance().get_plugins()
    assert len(registered) == 1
    assert registered[0].name == "sample_agent"


def test_agent_entry_invalid_class_does_not_register():
    """Class that is not an ToolPlugin subclass yields no registration, no crash."""
    manifest = PluginManifest(
        name="bad_pkg",
        version="0.0.0",
        description="",
        plugins=[
            PluginEntry(
                type_name="tool",
                name="bad",
                import_path=f"{__name__}:_NotAnAgent",
            ),
        ],
    )

    loader = _make_loader_with_agent_type()
    loader.load_manifest(manifest)
    loader.load_all_sync()

    # No registration on ToolRegistry. The exact failure path
    # (rejected during _load_plugin_class because not a PluginType
    # subclass, OR rejected by our `agent` branch's isinstance check)
    # depends on loader internals; either is acceptable as long as
    # no crash and no spurious registration.
    assert ToolRegistry.get_instance().get_plugins() == []
