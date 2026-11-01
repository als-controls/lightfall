"""Tests for folder plugins (real packages) in UserPluginService."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lightfall.plugins.user_plugins import UserPluginService
from lightfall.ui.panels.claude.tool_registry import ToolRegistry
from lightfall.ui.panels.registry import PanelRegistry


@pytest.fixture(autouse=True)
def reset_singletons():
    UserPluginService.reset_instance()
    ToolRegistry.reset_instance()
    PanelRegistry.reset()
    yield
    UserPluginService.reset_instance()
    ToolRegistry.reset_instance()
    PanelRegistry.reset()
    for key in [k for k in sys.modules if k.startswith("lightfall_user_plugins.")]:
        del sys.modules[key]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def service(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(
        "lightfall.plugins.types._user_plugin_roots",
        lambda: [tmp_path.resolve()],
    )
    svc = UserPluginService.get_instance()
    monkeypatch.setattr(svc, "_plugins_dir", tmp_path)
    return svc


INIT = '''
from pathlib import Path
from lightfall.plugins.tool_plugin import ToolPlugin
from . import fitting

PAGE = (Path(__file__).parent / "templates" / "x.html").read_text()

class FolderTool(ToolPlugin):
    @property
    def name(self): return "folder_tool"
    @property
    def description(self): return "fit=" + str(fitting.VALUE) + " page=" + PAGE
    def get_system_prompt(self): return "p"
'''


def _make_folder(root: Path, name: str = "xas", value: int = 1) -> Path:
    folder = root / name
    (folder / "templates").mkdir(parents=True)
    (folder / "templates" / "x.html").write_text("HTML", encoding="utf-8")
    (folder / "fitting.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
    (folder / "__init__.py").write_text(INIT, encoding="utf-8")
    return folder


def test_folder_plugin_relative_import_and_file(service, tmp_path):
    folder = _make_folder(tmp_path)
    assert service.load_plugin_from_file(folder, _skip_commit=True)
    plugin = ToolRegistry.get_instance().get_plugin("folder_tool")
    assert plugin is not None
    assert plugin.description == "fit=1 page=HTML"
    assert "lightfall_user_plugins.xas" in sys.modules
    assert "lightfall_user_plugins.xas.fitting" in sys.modules
    info = service.get_plugin_info(folder)
    assert [(r.registry_type, r.key) for r in info.registrations] == [
        ("tool", "folder_tool")
    ]


def test_file_and_folder_with_same_name_both_fail(service, tmp_path):
    _make_folder(tmp_path, "dup")
    (tmp_path / "dup.py").write_text("X = 1\n", encoding="utf-8")
    entries, errors = service._discover(tmp_path)
    assert entries == []
    assert {p for p, _ in errors} == {tmp_path / "dup.py", tmp_path / "dup"}
    results = service.load_all_plugins()
    assert all(err is not None for _, err in results)
    assert {p for p, _ in results} == {tmp_path / "dup.py", tmp_path / "dup"}
    assert ToolRegistry.get_instance().get_plugin("folder_tool") is None


def test_underscore_folder_skipped(service, tmp_path):
    _make_folder(tmp_path, "_hidden")
    (tmp_path / "nopkg").mkdir()  # no __init__.py: ignored
    (tmp_path / "real.py").write_text("X = 1\n", encoding="utf-8")
    entries, errors = service._discover(tmp_path)
    assert entries == [tmp_path / "real.py"]
    assert errors == []


def test_reload_purges_submodules(service, tmp_path):
    folder = _make_folder(tmp_path)
    assert service.load_plugin_from_file(folder, _skip_commit=True)
    (folder / "fitting.py").write_text("VALUE = 22\n", encoding="utf-8")
    assert service.reload_plugin(folder)
    plugin = ToolRegistry.get_instance().get_plugin("folder_tool")
    assert plugin.description == "fit=22 page=HTML"


def test_unload_unregisters_tools(service, tmp_path):
    folder = _make_folder(tmp_path)
    service.load_plugin_from_file(folder, _skip_commit=True)
    assert service.unload_plugin(folder)
    assert ToolRegistry.get_instance().get_plugin("folder_tool") is None
    assert not [k for k in sys.modules if k.startswith("lightfall_user_plugins.xas")]


def test_module_name_override_survives_reload(service, tmp_path):
    folder = _make_folder(tmp_path, "xas_tools@4")
    assert service.load_plugin_from_file(
        folder, _skip_commit=True, module_name="xas_tools"
    )
    assert "lightfall_user_plugins.xas_tools.fitting" in sys.modules
    assert service.reload_plugin(folder)
    assert "lightfall_user_plugins.xas_tools.fitting" in sys.modules
    assert ToolRegistry.get_instance().get_plugin("folder_tool") is not None


def test_failed_load_cleans_modules(service, tmp_path):
    folder = _make_folder(tmp_path)
    (folder / "fitting.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    assert not service.load_plugin_from_file(folder, _skip_commit=True)
    assert "boom" in service.get_plugin_info(folder).load_error
    assert not [k for k in sys.modules if k.startswith("lightfall_user_plugins.xas")]


def test_folder_commit_uses_folder_path(service, tmp_path, monkeypatch):
    folder = _make_folder(tmp_path)
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
    service.load_plugin_from_file(folder)
    assert calls == [("commit", [folder])]


def test_watcher_covers_folder_and_subfolders(service, tmp_path):
    folder = _make_folder(tmp_path)
    service.load_plugin_from_file(folder, _skip_commit=True)
    watched = set(service._watcher.files()) | set(service._watcher.directories())
    norm = {str(Path(w)) for w in watched}
    for p in (folder, folder / "templates", folder / "fitting.py",
              folder / "__init__.py", folder / "templates" / "x.html"):
        assert str(p) in norm


def test_change_in_folder_reloads_once_debounced(service, tmp_path, qtbot):
    folder = _make_folder(tmp_path)
    service.load_plugin_from_file(folder, _skip_commit=True)
    service.enable_hot_reload(True)
    reloads = []
    orig = service._reload_folder_plugin
    service._reload_folder_plugin = lambda p: (reloads.append(p), orig(p))[1]
    (folder / "fitting.py").write_text("VALUE = 555\n", encoding="utf-8")
    service._on_file_changed(str(folder / "fitting.py"))
    service._on_file_changed(str(folder / "__init__.py"))
    service._on_directory_changed(str(folder / "templates"))
    qtbot.waitUntil(lambda: len(reloads) >= 1, timeout=3000)
    qtbot.wait(500)
    assert reloads == [folder]
    assert ToolRegistry.get_instance().get_plugin("folder_tool").description == "fit=555 page=HTML"
