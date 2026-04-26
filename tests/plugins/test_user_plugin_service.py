"""Tests for UserPluginService with __init_subclass__-driven tracking."""
from __future__ import annotations

from pathlib import Path

import pytest

from lucid.plugins.user_plugins import UserPluginService
from lucid.ui.panels.claude.agent_registry import AgentRegistry


@pytest.fixture(autouse=True)
def reset_singletons():
    UserPluginService.reset_instance()
    AgentRegistry.reset_instance()
    yield
    UserPluginService.reset_instance()
    AgentRegistry.reset_instance()


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
