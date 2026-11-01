"""User plugins from synced scopes, and package plugins winning name clashes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lightfall.content.clashes import Clash, ClashLog
from lightfall.content.sources import SourceItem
from lightfall.plugins.tool_plugin import ToolPlugin
from lightfall.plugins.user_plugins import UserPluginService
from lightfall.ui.panels.claude.tool_registry import ToolRegistry
from lightfall.ui.panels.registry import PanelRegistry

BRANCH = "branch:7.0.1.1"


@pytest.fixture(autouse=True)
def reset_singletons():
    UserPluginService.reset_instance()
    ToolRegistry.reset_instance()
    PanelRegistry.reset()
    ClashLog.reset_instance()
    yield
    UserPluginService.reset_instance()
    ToolRegistry.reset_instance()
    PanelRegistry.reset()
    ClashLog.reset_instance()
    for key in [k for k in sys.modules if k.startswith("lightfall_user_plugins.")]:
        del sys.modules[key]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def local_dir(tmp_path):
    d = tmp_path / "plugins"
    d.mkdir()
    return d


@pytest.fixture
def content_dir(tmp_path):
    d = tmp_path / "content"
    d.mkdir()
    return d


@pytest.fixture
def service(tmp_path, local_dir, monkeypatch, qapp):
    monkeypatch.setattr(
        "lightfall.plugins.types._user_plugin_roots", lambda: [tmp_path.resolve()]
    )
    svc = UserPluginService.get_instance()
    monkeypatch.setattr(svc, "_plugins_dir", local_dir)
    return svc


@pytest.fixture
def commits(monkeypatch):
    calls = []

    class FakeTracker:
        def commit(self, paths, msg):
            calls.append(("commit", paths))

        def commit_removal(self, paths, msg):
            calls.append(("remove", paths))

    monkeypatch.setattr(
        "lightfall.plugins.user_plugins.GitTracker.get_instance",
        staticmethod(lambda: FakeTracker()),
    )
    return calls


def _tool_code(*tools: str, desc: str = "user", extra: str = "") -> str:
    lines = ["from lightfall.plugins.tool_plugin import ToolPlugin", extra]
    for i, name in enumerate(tools):
        lines.append(
            f"""
class _Tool{i}(ToolPlugin):
    @property
    def name(self): return "{name}"
    @property
    def description(self): return "{desc}"
    def get_system_prompt(self): return "p"
"""
        )
    return "\n".join(lines)


def _local_file(local_dir: Path, name: str, code: str) -> Path:
    path = local_dir / f"{name}.py"
    path.write_text(code, encoding="utf-8")
    return path


def _synced_file(content_dir: Path, name: str, code: str, scope: str = BRANCH,
                 rev: int = 3) -> SourceItem:
    folder = content_dir / scope.replace(":", "-") / "plugin" / f"{name}@{rev}"
    folder.mkdir(parents=True)
    entry = folder / f"{name}.py"
    entry.write_text(code, encoding="utf-8")
    return SourceItem("plugin", name, scope, entry, rev)


def _synced_folder(content_dir: Path, name: str, code: str, scope: str = BRANCH,
                   rev: int = 4) -> SourceItem:
    folder = content_dir / scope.replace(":", "-") / "plugin" / f"{name}@{rev}"
    folder.mkdir(parents=True)
    (folder / "helper.py").write_text("VALUE = 7\n", encoding="utf-8")
    (folder / "__init__.py").write_text(code, encoding="utf-8")
    return SourceItem("plugin", name, scope, folder, rev)


def _watched(service) -> set[str]:
    return {str(Path(p)) for p in (*service._watcher.files(), *service._watcher.directories())}


class _PackageTool(ToolPlugin):
    @property
    def name(self): return "shared_tool"
    @property
    def description(self): return "package"


def _load_package_tool() -> None:
    from lightfall.plugins.loader import PluginLoader
    from lightfall.plugins.manifest import PluginEntry, PluginManifest

    loader = PluginLoader()
    loader.register_plugin_type("tool", ToolPlugin)
    loader.load_manifest(PluginManifest(
        name="pkg", version="0.0.0", description="",
        plugins=[PluginEntry(type_name="tool", name="shared_tool",
                             import_path=f"{__name__}:_PackageTool")],
    ))
    loader.load_all_sync()


PANEL_ID = "lightfall.panels.shared"


def _self_registered_panel_code(*tools: str) -> str:
    return _tool_code(*(tools or ("panel_buddy",))) + f'''
from typing import ClassVar
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry

class UserSharedPanel(BasePanel):
    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(id="{PANEL_ID}", name="User")

PanelRegistry.get_instance().register(UserSharedPanel, replace=True)
'''


def _package_panel_class():
    from typing import ClassVar

    from lightfall.ui.panels.base import BasePanel, PanelMetadata

    class PackageSharedPanel(BasePanel):
        panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(id=PANEL_ID, name="Package")

    return PackageSharedPanel


# --- loading from synced scopes -------------------------------------------------


def test_branch_plugin_loads_with_source(service, content_dir, commits):
    item = _synced_file(content_dir, "beam_tools", _tool_code("beam_tool"))
    results = service.load_all_plugins(synced=[item])
    assert results == [(item.path, None)]
    assert ToolRegistry.get_instance().get_plugin("beam_tool") is not None
    info = service.get_plugin_info(item.path)
    assert info.source == BRANCH
    assert info.module_name == "lightfall_user_plugins.beam_tools"
    assert str(item.path) not in _watched(service)
    assert commits == []


def test_local_beats_branch_and_reports(service, local_dir, content_dir):
    item = _synced_file(content_dir, "x", _tool_code("x_tool", desc="branch"))
    local = _local_file(local_dir, "x", _tool_code("x_tool", desc="local"))
    service.load_all_plugins(synced=[item])
    assert ToolRegistry.get_instance().get_plugin("x_tool").description == "local"
    assert service.get_plugin_info(local).source == "local"
    assert service.get_plugin_info(item.path) is None
    assert ClashLog.get_instance().all() == [Clash("plugin", "x", "local", BRANCH, False)]


def test_more_specific_synced_scope_wins(service, content_dir):
    fac = _synced_file(content_dir, "y", _tool_code("y_tool", desc="fac"), scope="facility")
    br = _synced_file(content_dir, "y", _tool_code("y_tool", desc="branch"))
    service.load_all_plugins(synced=[fac, br])
    assert ToolRegistry.get_instance().get_plugin("y_tool").description == "branch"
    assert ClashLog.get_instance().all() == [Clash("plugin", "y", BRANCH, "facility", False)]


def test_synced_folder_plugin_named_by_listing(service, content_dir):
    code = _tool_code("xas_tool", extra="from .helper import VALUE")
    item = _synced_folder(content_dir, "xas_tools", code)
    assert item.path.name == "xas_tools@4"
    service.load_all_plugins(synced=[item])
    info = service.get_plugin_info(item.path)
    assert info.module_name == "lightfall_user_plugins.xas_tools"
    assert info.source == BRANCH
    assert "lightfall_user_plugins.xas_tools.helper" in sys.modules
    assert ToolRegistry.get_instance().get_plugin("xas_tool") is not None
    assert not (_watched(service) & {str(item.path), str(item.path / "__init__.py")})


def test_synced_plugin_with_error_reports_and_continues(service, local_dir, content_dir):
    bad = _synced_file(content_dir, "bad", "raise RuntimeError('boom')\n")
    good = _synced_file(content_dir, "good", _tool_code("good_tool"))
    local = _local_file(local_dir, "mine", _tool_code("mine_tool"))
    errors = []
    service.plugin_error.connect(lambda p, m: errors.append(p))
    results = dict(service.load_all_plugins(synced=[bad, good]))
    assert isinstance(results[bad.path], Exception)
    assert "boom" in str(results[bad.path])
    assert results[good.path] is None and results[local] is None
    assert errors == [str(bad.path)]
    assert ToolRegistry.get_instance().get_plugin("good_tool") is not None
    assert ToolRegistry.get_instance().get_plugin("mine_tool") is not None


def test_synced_plugin_classes_auto_enqueue(content_dir, local_dir, monkeypatch, qapp):
    # No _user_plugin_roots patch: the real roots must include the content dir.
    monkeypatch.setattr("lightfall.content.store.default_content_dir", lambda: content_dir)
    from lightfall.plugins.types import _user_plugin_roots
    assert content_dir.resolve() in _user_plugin_roots()

    svc = UserPluginService.get_instance()
    monkeypatch.setattr(svc, "_plugins_dir", local_dir)
    item = _synced_file(content_dir, "auto", _tool_code("auto_tool"))
    svc.load_all_plugins(synced=[item])
    assert ToolRegistry.get_instance().get_plugin("auto_tool") is not None
    regs = svc.get_plugin_info(item.path).registrations
    assert [(r.registry_type, r.key) for r in regs] == [("tool", "auto_tool")]


def test_default_resolver_is_used_when_synced_omitted(service, content_dir, monkeypatch):
    item = _synced_file(content_dir, "dflt", _tool_code("dflt_tool"))
    from lightfall.plugins import user_plugins
    monkeypatch.setattr(user_plugins, "default_synced_items",
                        lambda type_: [item] if type_ == "plugin" else [])
    service.load_all_plugins()
    assert ToolRegistry.get_instance().get_plugin("dflt_tool") is not None


# --- local rescans leave synced plugins alone ------------------------------------


def test_local_rescan_keeps_synced_plugins(service, local_dir, content_dir):
    item = _synced_file(content_dir, "remote", _tool_code("remote_tool"))
    service.load_all_plugins(synced=[item])
    service.enable_hot_reload(True)
    new_local = _local_file(local_dir, "fresh", _tool_code("fresh_tool"))
    service._on_directory_changed(str(local_dir))
    assert service.get_plugin_info(item.path) is not None
    assert ToolRegistry.get_instance().get_plugin("remote_tool") is not None
    assert service.get_plugin_info(new_local) is not None


def test_new_local_file_displaces_loaded_synced_plugin(service, local_dir, content_dir):
    item = _synced_file(content_dir, "z", _tool_code("z_tool", desc="branch"))
    service.load_all_plugins(synced=[item])
    service.enable_hot_reload(True)
    local = _local_file(local_dir, "z", _tool_code("z_tool", desc="local"))
    service._on_directory_changed(str(local_dir))
    assert service.get_plugin_info(item.path) is None
    assert service.get_plugin_info(local).source == "local"
    assert ToolRegistry.get_instance().get_plugin("z_tool").description == "local"
    assert ClashLog.get_instance().all() == [Clash("plugin", "z", "local", BRANCH, True)]


# --- package plugins win on registration ---------------------------------------


def test_package_tool_displaces_whole_user_plugin(service, local_dir):
    path = _local_file(local_dir, "mixed",
                       _self_registered_panel_code("shared_tool", "other_tool"))
    assert service.load_plugin_from_file(path, _skip_commit=True)
    tools = ToolRegistry.get_instance()
    assert tools.get_plugin("shared_tool").description == "user"
    assert PanelRegistry.get_instance().get(PANEL_ID) is not None

    _load_package_tool()

    assert isinstance(tools.get_plugin("shared_tool"), _PackageTool)
    assert tools.get_plugin("other_tool") is None
    assert PanelRegistry.get_instance().get(PANEL_ID) is None
    info = service.get_plugin_info(path)
    assert info is None or (info.load_error and not info.registrations)
    assert service.owner_of("tool", "shared_tool") is None
    assert ClashLog.get_instance().all() == [
        Clash("plugin", "shared_tool", "package", "local", False)
    ]


def test_package_panel_displaces_user_plugin(service, local_dir):
    from lightfall.plugins.info import PluginInfo as LoaderInfo
    from lightfall.plugins.loader import PluginLoader

    path = _local_file(local_dir, "panelish", _self_registered_panel_code())
    assert service.load_plugin_from_file(path, _skip_commit=True)
    assert service.owner_of("panel", PANEL_ID) == str(path)

    pkg_cls = _package_panel_class()

    class _PanelPlugin:
        def get_panel_class(self):
            return pkg_cls

    fake = LoaderInfo(type_name="panel", name="pkg_panel", import_path="x:y",
                      instance=_PanelPlugin())
    PluginLoader()._register_with_type_registry(fake)

    assert PanelRegistry.get_instance().get(PANEL_ID) is pkg_cls
    assert ToolRegistry.get_instance().get_plugin("panel_buddy") is None
    assert ClashLog.get_instance().all() == [
        Clash("plugin", PANEL_ID, "package", "local", False)
    ]


def test_user_plugin_refused_when_package_owns_tool(service, local_dir):
    ToolRegistry.get_instance().register(_PackageTool())
    path = _local_file(local_dir, "late", _tool_code("shared_tool", "late_extra"))
    errors = []
    service.plugin_error.connect(lambda p, m: errors.append(p))
    assert service.load_plugin_from_file(path, _skip_commit=True) is False
    tools = ToolRegistry.get_instance()
    assert isinstance(tools.get_plugin("shared_tool"), _PackageTool)
    assert tools.get_plugin("late_extra") is None
    info = service.get_plugin_info(path)
    assert info.load_error and not info.registrations
    assert errors == [str(path)]
    assert ClashLog.get_instance().all() == [
        Clash("plugin", "shared_tool", "package", "local", False)
    ]
    # a later rescan of the local dir does not retry it
    service.enable_hot_reload(True)
    service._on_directory_changed(str(local_dir))
    assert tools.get_plugin("late_extra") is None


def test_self_registered_panel_refused_when_package_owns_it(service, local_dir):
    pkg_cls = _package_panel_class()
    PanelRegistry.get_instance().register(pkg_cls)
    path = _local_file(local_dir, "late_panel", _self_registered_panel_code())
    assert service.load_plugin_from_file(path, _skip_commit=True) is False
    assert PanelRegistry.get_instance().get(PANEL_ID) is pkg_cls
    assert ToolRegistry.get_instance().get_plugin("panel_buddy") is None
    assert ClashLog.get_instance().all() == [
        Clash("plugin", PANEL_ID, "package", "local", False)
    ]


def test_imported_package_panel_is_not_tracked_as_user_registration(service, local_dir):
    pkg_cls = _package_panel_class()
    PanelRegistry.get_instance().register(pkg_cls)
    sys.modules["_fake_pkg_panels"] = type(sys)("_fake_pkg_panels")
    sys.modules["_fake_pkg_panels"].PackageSharedPanel = pkg_cls
    try:
        path = _local_file(local_dir, "importer",
                           "from _fake_pkg_panels import PackageSharedPanel\n")
        assert service.load_plugin_from_file(path, _skip_commit=True)
        assert service.get_plugin_info(path).registrations == []
        service.unload_plugin(path)
        assert PanelRegistry.get_instance().get(PANEL_ID) is pkg_cls
    finally:
        del sys.modules["_fake_pkg_panels"]
