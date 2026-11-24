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


def test_background_yields_register_a_ready_plugin_once():
    """A ready plugin processed twice registers once, with no self-clash.

    The background generator yields each plugin after import and again after
    init. When the main thread is stalled, both queued yields are handled
    after the plugin is ready; the second used to re-register the plan and
    report "package kept, package not used".
    """
    from lightfall.content.clashes import ClashLog

    ClashLog.reset_instance()
    try:
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
        infos = list(loader._load_plugins_generator())  # import yield, init yield
        assert len(infos) == 2 and all(i.is_ready for i in infos)

        loaded = []
        loader.plugin_loaded.connect(loaded.append)
        for info in infos:
            loader._on_plugin_processed(info)

        assert len(loaded) == 1
        assert "sample_plan" in PlanRegistry.get_instance()
        assert ClashLog.get_instance().all() == []
    finally:
        ClashLog.reset_instance()


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


class _IconPlan(_SamplePlan):
    @property
    def name(self) -> str:
        return "icon_plan"

    @property
    def category(self) -> str:
        return "rixs"  # not in PLAN_CATEGORY_ICONS

    @property
    def display_name(self) -> str:
        return "Fancy Icon Plan"

    @property
    def icon(self) -> tuple[str, str]:
        return ("#3F51B5", "Q")


def _load_single(import_name: str, plan_name: str) -> None:
    manifest = PluginManifest(
        name="test_pkg",
        version="0.0.0",
        description="",
        plugins=[
            PluginEntry(
                type_name="plan",
                name=plan_name,
                import_path=f"{__name__}:{import_name}",
            ),
        ],
    )
    loader = PluginLoader()
    loader.register_plugin_type("plan", PlanPlugin)
    loader.load_manifest(manifest)
    assert loader.load_all_sync() == (1, 0)


def test_plugin_icon_and_display_name_reach_registry():
    """icon/display_name declared on a PlanPlugin survive registration."""
    _load_single("_IconPlan", "icon_plan")

    info = PlanRegistry.get_instance().get_plan("icon_plan")
    assert info.get_icon() == ("#3F51B5", "Q")
    assert info.get_display_name() == "Fancy Icon Plan"


def test_plugin_without_icon_uses_category_default():
    _load_single("_SamplePlan", "sample_plan")

    info = PlanRegistry.get_instance().get_plan("sample_plan")
    assert info.icon is None
    assert info.get_icon() == ("#4CAF50", "S")  # "scan" category default
    assert info.get_display_name() == "Sample Plan"


@pytest.mark.parametrize("bad", ["#fff", ("#fff",), ("#fff", ""), ("#fff", 1)])
def test_invalid_icon_raises(bad):
    class _BadIcon(_SamplePlan):
        @property
        def icon(self):
            return bad

    with pytest.raises(ValueError, match="icon must be"):
        _BadIcon().get_plan_info()


class _DescribedPlan(_SamplePlan):
    @property
    def name(self) -> str:
        return "described_plan"

    @property
    def plan_description(self) -> str:
        return "Overridden description."


def test_plan_description_override_reaches_registry():
    _load_single("_DescribedPlan", "described_plan")

    info = PlanRegistry.get_instance().get_plan("described_plan")
    assert info.description == "Overridden description."


def test_default_plan_description_uses_docstring_first_paragraph():
    _load_single("_SamplePlan", "sample_plan")

    info = PlanRegistry.get_instance().get_plan("sample_plan")
    assert info.description == "A sample plan."
