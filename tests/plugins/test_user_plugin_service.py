"""Tests for UserPluginService with __init_subclass__-driven tracking."""
from __future__ import annotations

from pathlib import Path

import pytest

from lucid.plugins.user_plugins import UserPluginService
from lucid.ui.panels.claude.agent_registry import AgentRegistry
from lucid.ui.panels.registry import PanelRegistry


@pytest.fixture(autouse=True)
def reset_singletons():
    UserPluginService.reset_instance()
    AgentRegistry.reset_instance()
    PanelRegistry.reset()
    yield
    UserPluginService.reset_instance()
    AgentRegistry.reset_instance()
    PanelRegistry.reset()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def fake_user_dir(tmp_path, monkeypatch):
    """Make tmp_path act as the canonical user plugin dir."""
    monkeypatch.setattr(
        "lucid.plugins.types._user_plugin_roots",
        lambda: [tmp_path.resolve()],
    )
    # Also force UserPluginService to use this dir
    return tmp_path


def _write_user_agent(dir_: Path, name: str, suffix: str = "") -> Path:
    path = dir_ / f"{name}.py"
    path.write_text(
        f'''
from lucid.plugins.agent_plugin import AgentPlugin

class {name.title().replace("_", "")}Agent(AgentPlugin):
    @property
    def name(self): return "{name}"
    @property
    def description(self): return "user-contributed {name}{suffix}"
    def get_system_prompt(self): return "## {name} prompt"
''',
        encoding="utf-8",
    )
    return path


def test_load_plugin_registers_with_agent_registry(fake_user_dir, monkeypatch):
    """Defining an AgentPlugin subclass in a user file auto-registers it."""
    # Make UserPluginService treat fake_user_dir as the plugin dir
    service = UserPluginService.get_instance()
    monkeypatch.setattr(service, "_plugins_dir", fake_user_dir)

    path = _write_user_agent(fake_user_dir, "user_alpha")
    success = service.load_plugin_from_file(path)
    assert success
    assert AgentRegistry.get_instance().get_plugin("user_alpha") is not None


def test_unload_removes_from_agent_registry(fake_user_dir, monkeypatch):
    service = UserPluginService.get_instance()
    monkeypatch.setattr(service, "_plugins_dir", fake_user_dir)

    path = _write_user_agent(fake_user_dir, "user_beta")
    service.load_plugin_from_file(path)
    assert AgentRegistry.get_instance().get_plugin("user_beta") is not None

    service.unload_plugin(path)
    assert AgentRegistry.get_instance().get_plugin("user_beta") is None


def _write_user_panel(dir_: Path, name: str) -> Path:
    """Write a user panel plugin: PanelPlugin + BasePanel pair, the
    canonical contract documented in lucid/plugins/panel_plugin.py."""
    path = dir_ / f"{name}.py"
    cls_stem = name.title().replace("_", "")
    path.write_text(
        f'''
from typing import ClassVar

from lucid.plugins.panel_plugin import PanelPlugin
from lucid.ui.panels.base import BasePanel, PanelMetadata


class {cls_stem}Panel(BasePanel):
    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.user.{name}",
        name="{cls_stem} Panel",
    )


class {cls_stem}PanelPlugin(PanelPlugin):
    @property
    def name(self): return "{name}"
    def get_panel_class(self): return {cls_stem}Panel
''',
        encoding="utf-8",
    )
    return path


def test_load_user_panel_registers_basepanel_class(fake_user_dir, monkeypatch, qapp):
    """User PanelPlugin file: the BasePanel subclass (not the PanelPlugin) lands
    in PanelRegistry under panel_metadata.id. Regression test for the Spec A
    rewrite that registered cls (the PanelPlugin) by mistake."""
    service = UserPluginService.get_instance()
    monkeypatch.setattr(service, "_plugins_dir", fake_user_dir)

    path = _write_user_panel(fake_user_dir, "user_delta")
    success = service.load_plugin_from_file(path)
    assert success

    panel_id = "lucid.panels.user.user_delta"
    panel_ids = PanelRegistry.get_instance().list_panel_ids()
    assert panel_id in panel_ids, f"expected {panel_id} in registry, got {panel_ids}"


def test_unload_user_panel_removes_basepanel_from_registry(fake_user_dir, monkeypatch, qapp):
    service = UserPluginService.get_instance()
    monkeypatch.setattr(service, "_plugins_dir", fake_user_dir)

    path = _write_user_panel(fake_user_dir, "user_epsilon")
    service.load_plugin_from_file(path)
    panel_id = "lucid.panels.user.user_epsilon"
    assert panel_id in PanelRegistry.get_instance().list_panel_ids()

    service.unload_plugin(path)
    assert panel_id not in PanelRegistry.get_instance().list_panel_ids()


def test_reload_replaces_old_registration(fake_user_dir, monkeypatch):
    service = UserPluginService.get_instance()
    monkeypatch.setattr(service, "_plugins_dir", fake_user_dir)

    path = _write_user_agent(fake_user_dir, "user_gamma", suffix=" v1")
    service.load_plugin_from_file(path)
    first = AgentRegistry.get_instance().get_plugin("user_gamma")
    assert first is not None
    assert "v1" in first.description

    # Edit the file: change description
    path.write_text(
        path.read_text().replace("user-contributed user_gamma v1", "user-contributed user_gamma v2"),
        encoding="utf-8",
    )
    service.reload_plugin(path)

    second = AgentRegistry.get_instance().get_plugin("user_gamma")
    assert second is not None
    assert second is not first
    assert "v2" in second.description
