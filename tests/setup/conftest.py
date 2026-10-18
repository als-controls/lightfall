"""Fixtures for the setup wizard tests: a real ConfigManager on temp dirs."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def config_dirs(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    programdata = tmp_path / "programdata"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(appdata))
    monkeypatch.setenv("PROGRAMDATA", str(programdata))
    return appdata, programdata


@pytest.fixture
def cfg(config_dirs):
    from lightfall.config import ConfigManager

    return ConfigManager()


@pytest.fixture
def prefs(qapp, cfg, monkeypatch):
    from lightfall.settings import user_settings_client as usc
    from lightfall.ui.preferences.manager import PreferencesManager

    fake = MagicMock()
    fake.get_all.return_value = {}
    monkeypatch.setattr(usc.UserSettingsClient, "get_instance", classmethod(lambda cls: fake))
    PreferencesManager.reset()
    manager = PreferencesManager.get_instance()
    manager.set_config_manager(cfg)
    yield manager
    PreferencesManager.reset()


@pytest.fixture
def ctx(cfg, prefs):
    from lightfall.auth.session import SessionManager
    from lightfall.setup.context import SetupContext

    SessionManager.reset()
    yield SetupContext(config=cfg, session=SessionManager.get_instance(), prefs=prefs)
    SessionManager.reset()
