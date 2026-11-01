"""Tests for the `plan` plugin type: builtin registration + loader branch."""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from lightfall.acquire.plans.registry import PlanRegistry
from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.manifest import PluginEntry, PluginManifest
from lightfall.plugins.plan_plugin import PlanPlugin


class _SamplePlan(PlanPlugin):
    @property
    def name(self) -> str:
        return "sample_plan"

    @property
    def category(self) -> str:
        return "scan"

    def get_plan_function(self):
        def _plan(detectors, motor, start: float, stop: float, num: int = 11) -> Generator[Any, Any, Any]:
            """A sample plan."""
            yield from ()

        return _plan


@pytest.fixture(autouse=True)
def reset_plan_registry():
    PlanRegistry.reset_instance()
    yield
    PlanRegistry.reset_instance()


def test_builtin_plugin_types_include_plan():
    """The app's builtin plugin-type registration must include 'plan'.

    Regression guard: without this, manifest entries with type_name='plan'
    are silently skipped by the loader (_process_manifest), so beamline /
    user plan plugins never reach the PlanRegistry or the UI.
    """
    from lightfall.main import _register_builtin_plugin_types

    loader = PluginLoader()
    _register_builtin_plugin_types(loader)

    assert loader.get_plugin_type("plan") is PlanPlugin
    # The fix must not drop the types that already worked.
    for existing in ("theme", "settings", "engine", "device_backend", "panel"):
        assert loader.get_plugin_type(existing) is not None


def test_plan_entry_registers_with_plan_registry():
    """A manifest entry with type_name='plan' registers with the PlanRegistry."""
    manifest = PluginManifest(
        name="test_pkg",
        version="0.0.0",
        description="",
        plugins=[
            PluginEntry(
                type_name="plan",
                name="sample_plan",
                import_path=f"{__name__}:_SamplePlan",
            ),
        ],
    )

    loader = PluginLoader()
    loader.register_plugin_type("plan", PlanPlugin)
    loader.load_manifest(manifest)
    successful, failed = loader.load_all_sync()

    assert (successful, failed) == (1, 0)
    assert "sample_plan" in PlanRegistry.get_instance()


def test_package_plan_displaces_user_plan_and_reports():
    """A package plan replaces a same-named local plan; the clash is logged."""
    from lightfall.content.clashes import Clash, ClashLog

    ClashLog.reset_instance()
    try:
        registry = PlanRegistry.get_instance()
        registry.register_ranked("sample_plan", _SamplePlan().get_plan_function(), "scan", "local")

        manifest = PluginManifest(
            name="test_pkg",
            version="0.0.0",
            description="",
            plugins=[
                PluginEntry(
                    type_name="plan",
                    name="sample_plan",
                    import_path=f"{__name__}:_SamplePlan",
                ),
            ],
        )
        loader = PluginLoader()
        loader.register_plugin_type("plan", PlanPlugin)
        loader.load_manifest(manifest)
        assert loader.load_all_sync() == (1, 0)

        assert registry.get_plan("sample_plan").source == "package"
        assert ClashLog.get_instance().all() == [
            Clash("plan", "sample_plan", "package", "local", False)
        ]
    finally:
        ClashLog.reset_instance()
