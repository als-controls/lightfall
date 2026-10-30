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


def test_bind_survives_a_bad_preference(config_dirs, monkeypatch):
    """One invalid value (e.g. ``theme: null`` left by an old remove()) must not
    reset the whole config to defaults and drop the binding."""
    cm = ConfigManager()
    cm.ensure_user_config_dir()
    _bind_with(cm, monkeypatch, "branch:7.0.1.1")
    cm.set("preferences.theme", None, persist=True)
    cm.set("preferences.font_size", "huge", persist=True)
    cm.set("preferences.show_toolbar", False, persist=True)

    fresh = ConfigManager()
    assert fresh.model.site.bind == "branch:7.0.1.1"
    assert fresh.model.preferences.theme == "system"  # the field default
    assert fresh.model.preferences.font_size == 10
    assert fresh.model.preferences.show_toolbar is False  # valid values kept
    assert fresh.validation_errors  # still reported


def test_unset_deletes_the_key_from_the_user_file(config_dirs):
    cm = ConfigManager()
    cm.ensure_user_config_dir()
    cm.set("preferences.theme", "dark", persist=True)
    cm.unset("preferences.theme")
    cm.unset("preferences.not_there")  # no-op
    cm.unset("nowhere.at.all")  # no-op
    text = cm.get_user_config_path().read_text()
    assert "theme" not in text
    fresh = ConfigManager()
    assert fresh.model.preferences.theme == "system"
    assert not fresh.validation_errors
