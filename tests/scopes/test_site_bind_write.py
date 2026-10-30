"""set_site_bind() writes the user config layer, and a fresh start reads it back."""
from __future__ import annotations

import pytest

from lightfall.config import ConfigManager


@pytest.fixture
def config_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    # The global layer is /etc/<app> on Linux whatever the environment says.
    monkeypatch.setattr("lightfall_utils.config.manager._global_config_dir",
                        lambda app: tmp_path / "global" / app)
    return tmp_path


def _bind_with(cm, monkeypatch, bind):
    from lightfall.scopes import site

    class App:
        class services:  # noqa: N801 - mimics LFApplication.services
            @staticmethod
            def get(_cls):
                return cm

    monkeypatch.setattr("lightfall.core.LFApplication.get_instance", lambda: App)
    site.set_site_bind(bind)


def test_bind_then_unbind_survive_a_restart(config_dirs, monkeypatch):
    cm = ConfigManager()
    cm.ensure_user_config_dir()
    _bind_with(cm, monkeypatch, "branch:12.0.2.1")
    assert ConfigManager().model.site.bind == "branch:12.0.2.1"
    _bind_with(cm, monkeypatch, "facility")
    assert ConfigManager().model.site.bind is None


def test_ui_binding_overrides_a_provisioned_global_one(config_dirs, monkeypatch):
    global_dir = config_dirs / "global" / "ncs"
    global_dir.mkdir(parents=True)
    (global_dir / "application.yaml").write_text("site:\n  bind: branch:7.0.1.1\n",
                                                  encoding="utf-8")
    cm = ConfigManager()
    cm.ensure_user_config_dir()
    assert cm.model.site.bind == "branch:7.0.1.1"
    _bind_with(cm, monkeypatch, "branch:12.0.2.1")
    assert ConfigManager().model.site.bind == "branch:12.0.2.1"
    _bind_with(cm, monkeypatch, "facility")
    assert ConfigManager().model.site.bind is None


def test_binding_names_are_the_bind_then_its_beamline(monkeypatch):
    from lightfall.scopes import site
    from lightfall.ui.preferences.manager import PreferencesManager, ScopeInfo

    class Manager:
        scope_info = ScopeInfo("server", ("facility", "beamline:7.0.1", "branch:7.0.1.1",
                                          "user:u1"))

    monkeypatch.setattr(PreferencesManager, "get_instance", lambda: Manager)
    monkeypatch.setattr(site, "get_site_bind", lambda: "branch:7.0.1.1")
    assert site.binding_names() == ["7.0.1.1", "7.0.1"]
    monkeypatch.setattr(site, "get_site_bind", lambda: "beamline:7.0.1")
    assert site.binding_names() == ["7.0.1"]
    monkeypatch.setattr(site, "get_site_bind", lambda: "facility")
    assert site.binding_names() == []
