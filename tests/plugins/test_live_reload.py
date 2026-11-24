"""Live reload of manifest plugins (installed packages and core): panels and settings pages.

The plugin modules are real files under tmp_path, so ``importlib.reload`` really
re-executes edited source. The plugin/panel registries and the main window's
docking manager are small fakes recording what the reload did to them.
"""

from __future__ import annotations

import sys
import textwrap
from types import SimpleNamespace

import pytest

from lightfall.plugins.info import PluginInfo, PluginStatus
from lightfall.plugins.live_reload import (
    LiveReloadError,
    reload_installed_plugin,
    resolve_target,
)

PKG = "lr_fake_plugin"
PANEL_ID = "lr_fake.panels.demo"

PANEL_SRC = '''
from lightfall.plugins.panel_plugin import PanelPlugin
from lightfall.ui.panels.base import BasePanel, PanelMetadata

VERSION = {version!r}


class DemoPanel(BasePanel):
    panel_metadata = PanelMetadata(id={panel_id!r}, name="Demo", default_area="bottom")

    def _setup_ui(self):
        self.version = VERSION


class DemoPanelPlugin(PanelPlugin):
    @property
    def name(self):
        return "demo_panel"

    def get_panel_class(self):
        return DemoPanel


def service_factory():
    return VERSION
'''

SETTINGS_SRC = '''
from lightfall.plugins.settings_plugin import SettingsPlugin

VERSION = {version!r}


class DemoSettings(SettingsPlugin):
    @property
    def name(self):
        return "demo"

    def create_widget(self, parent=None):
        return None
'''

HELPER_SRC = "VALUE = {version!r}\n"


@pytest.fixture(autouse=True)
def _fresh_spec_registry():
    from lightfall.scopes.keys import _reset_client_registry

    _reset_client_registry()
    yield
    _reset_client_registry()


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    """A package on sys.path with panel/settings/helper modules; ``write`` edits them."""
    root = tmp_path / PKG
    root.mkdir()
    (root / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # no stale .pyc on fast edits

    def write(module: str, src: str, **fmt) -> None:
        (root / f"{module}.py").write_text(textwrap.dedent(src.format(**fmt)))

    write("panel", PANEL_SRC, version="v1", panel_id=PANEL_ID)
    write("settings", SETTINGS_SRC, version="v1")
    write("helper", HELPER_SRC, version="v1")
    yield SimpleNamespace(write=write)
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]


def _info(type_name: str, name: str, import_path: str) -> PluginInfo:
    """A READY PluginInfo, loaded the way PluginLoader would."""
    import importlib

    module_path, class_name = import_path.rsplit(":", 1)
    cls = getattr(importlib.import_module(module_path), class_name)
    return PluginInfo(
        type_name=type_name, name=name, import_path=import_path,
        plugin_class=cls, instance=cls(), status=PluginStatus.READY,
        manifest_name="lr-fake",
    )


class FakePluginRegistry:
    def __init__(self, manifests: dict[str, list[PluginInfo]]):
        self.manifests = manifests

    def get_manifests(self):
        return list(self.manifests)

    def get_plugins_from_manifest(self, name):
        return list(self.manifests.get(name, []))


class FakePanelRegistry:
    def __init__(self):
        self.types: dict[str, type] = {}
        self.destroyed: list[str] = []

    def register(self, cls, *, replace=False):
        self.types[cls.panel_metadata.id] = cls

    def unregister(self, panel_id):
        return self.types.pop(panel_id, None) is not None

    def destroy_singleton(self, panel_id):
        self.destroyed.append(panel_id)
        return True


class FakeDock:
    def __init__(self, panel, visible):
        self.panel, self._visible = panel, visible

    def isVisible(self):
        return self._visible


class FakeDocking:
    def __init__(self):
        self.docks: dict[str, FakeDock] = {}
        self._panel_areas: dict[str, str] = {}
        self.shown: list[str] = []

    def get_dock_widget(self, panel_id):
        return self.docks.get(panel_id)

    def remove_panel(self, panel_id):
        self._panel_areas.pop(panel_id, None)
        return self.docks.pop(panel_id, None) is not None

    def show_panel(self, panel_id):
        self.shown.append(panel_id)
        self.docks[panel_id]._visible = True
        return True


class FakeWindow:
    """Builds panels from the panel registry, as LFMainWindow.add_panel does."""

    def __init__(self, panel_registry):
        self._docking_manager = FakeDocking()
        self._panel_registry = panel_registry
        self.added: list[tuple[str, str | None]] = []

    def open(self, panel_id, area, visible):
        panel = self._panel_registry.types[panel_id]()
        self._docking_manager.docks[panel_id] = FakeDock(panel, visible)
        self._docking_manager._panel_areas[panel_id] = area
        return panel

    def add_panel(self, panel_id, area=None):
        self.added.append((panel_id, area))
        panel = self._panel_registry.types[panel_id]()
        self._docking_manager.docks[panel_id] = FakeDock(panel, False)
        self._docking_manager._panel_areas[panel_id] = area
        return panel


@pytest.fixture
def setup(pkg, qtbot):
    panel_info = _info("panel", "demo_panel", f"{PKG}.panel:DemoPanelPlugin")
    settings_info = _info("settings", "demo", f"{PKG}.settings:DemoSettings")
    service_info = PluginInfo(
        type_name="service", name="demo_service",
        import_path=f"{PKG}.panel:service_factory",
        instance=object(), status=PluginStatus.READY, manifest_name="lr-fake",
    )
    plugins = FakePluginRegistry({"lr-fake": [panel_info, settings_info, service_info]})
    panels = FakePanelRegistry()
    panels.register(panel_info.instance.get_panel_class())
    window = FakeWindow(panels)

    def reload(target="lr-fake", **kw):
        return reload_installed_plugin(
            target, plugin_registry=plugins, panel_registry=panels, window=window, **kw
        )

    return SimpleNamespace(
        pkg=pkg, plugins=plugins, panels=panels, window=window, reload=reload,
        panel_info=panel_info, settings_info=settings_info,
    )


# --- resolving the target ---------------------------------------------------------------


def test_resolves_manifest_entry_and_panel_id(setup):
    reg = setup.plugins
    assert len(resolve_target("lr-fake", reg)[1]) == 3
    assert resolve_target("lr-fake:demo_panel", reg)[1] == [setup.panel_info]
    assert resolve_target("lr-fake:settings:demo", reg)[1] == [setup.settings_info]
    assert resolve_target(PANEL_ID, reg) == ("lr-fake", [setup.panel_info])


def test_unknown_target_raises_and_lists_manifests(setup):
    with pytest.raises(LiveReloadError, match="lr-fake"):
        resolve_target("nope", setup.plugins)
    with pytest.raises(LiveReloadError, match="No entry"):
        resolve_target("lr-fake:nope", setup.plugins)


def test_ambiguous_entry_name_asks_for_the_type(setup):
    twin = PluginInfo(type_name="tool", name="demo", import_path="x:y", manifest_name="lr-fake")
    setup.plugins.manifests["lr-fake"].append(twin)
    with pytest.raises(LiveReloadError, match="ambiguous"):
        resolve_target("lr-fake:demo", setup.plugins)


# --- panels ---------------------------------------------------------------------------------


def test_open_panel_is_rebuilt_from_edited_source_in_the_same_place(setup, qtbot):
    old = setup.window.open(PANEL_ID, "left", visible=True)
    qtbot.addWidget(old)
    setup.pkg.write("panel", PANEL_SRC, version="v2", panel_id=PANEL_ID)

    report = setup.reload("lr-fake:demo_panel")

    assert report.success, report.errors
    assert report.modules == [f"{PKG}.panel"]
    assert setup.panels.destroyed == [PANEL_ID]  # cached singleton dropped
    assert setup.window.added == [(PANEL_ID, "left")]  # same dock area
    assert setup.window._docking_manager.shown == [PANEL_ID]  # was showing
    new = setup.window._docking_manager.docks[PANEL_ID].panel
    qtbot.addWidget(new)
    assert new.version == "v2" and type(new) is not type(old)
    assert setup.panels.types[PANEL_ID] is type(new)
    assert setup.panel_info.instance.get_panel_class() is type(new)
    assert report.reloaded == [{
        "entry": "panel:demo_panel", "panel_id": PANEL_ID, "was_open": True, "reopened": True,
    }]


def test_hidden_panel_is_reopened_but_stays_hidden(setup, qtbot):
    qtbot.addWidget(setup.window.open(PANEL_ID, "bottom", visible=False))
    report = setup.reload(PANEL_ID)
    assert report.success
    assert setup.window.added == [(PANEL_ID, "bottom")]
    assert setup.window._docking_manager.shown == []


def test_closed_panel_only_gets_the_new_class(setup):
    setup.pkg.write("panel", PANEL_SRC, version="v2", panel_id=PANEL_ID)
    report = setup.reload(PANEL_ID)
    assert report.success
    assert setup.window.added == []
    assert report.reloaded[0]["was_open"] is False
    assert sys.modules[f"{PKG}.panel"].VERSION == "v2"


def test_broken_edit_touches_no_panel(setup, qtbot):
    old = setup.window.open(PANEL_ID, "left", visible=True)
    qtbot.addWidget(old)
    old_class = setup.panels.types[PANEL_ID]
    setup.pkg.write("panel", "def broken(:\n")

    report = setup.reload(PANEL_ID)

    assert not report.success
    assert "SyntaxError" in report.errors[0]
    assert setup.window._docking_manager.docks[PANEL_ID].panel is old  # still open
    assert setup.panels.types[PANEL_ID] is old_class
    assert setup.panels.destroyed == []


def test_extra_modules_are_reimported_first(setup):
    import importlib

    importlib.import_module(f"{PKG}.helper")
    setup.pkg.write("helper", HELPER_SRC, version="v2")
    report = setup.reload(PANEL_ID, extra_modules=[f"{PKG}.helper"])
    assert report.modules == [f"{PKG}.helper", f"{PKG}.panel"]
    assert sys.modules[f"{PKG}.helper"].VALUE == "v2"


def test_assistant_panel_is_never_reloaded(setup, monkeypatch):
    import lightfall.plugins.live_reload as lr

    monkeypatch.setattr(lr, "PROTECTED_PANELS", frozenset({PANEL_ID}))
    report = setup.reload(PANEL_ID)
    assert not report.success
    assert report.reloaded == [] and report.modules == []
    assert "Assistant panel" in report.skipped[0]["reason"]


# --- settings pages and everything else ---------------------------------------------------


def test_settings_page_gets_a_fresh_instance(setup):
    old = setup.settings_info.instance
    setup.pkg.write("settings", SETTINGS_SRC, version="v2")
    report = setup.reload("lr-fake:demo")
    assert report.success
    new = setup.settings_info.instance
    assert new is not old and type(new) is not type(old)
    assert sys.modules[f"{PKG}.settings"].VERSION == "v2"
    assert "on_loaded() was not re-run" in report.reloaded[0]["note"]


def test_other_kinds_need_a_restart_and_shared_modules_are_flagged(setup):
    report = setup.reload("lr-fake")
    assert report.success
    assert {r["entry"] for r in report.reloaded} == {"panel:demo_panel", "settings:demo"}
    assert report.skipped == [{
        "entry": "service:demo_service",
        "reason": "restart needed (service plugins are not live-reloadable)",
    }]
    # The service lives in the re-imported panel module: it keeps the old code.
    assert any("service:demo_service" in w for w in report.warnings)


def test_nothing_reloadable_is_an_error(setup):
    report = setup.reload("lr-fake:service:demo_service")
    assert not report.success
    assert report.errors == ["Nothing live-reloadable in this target"]
