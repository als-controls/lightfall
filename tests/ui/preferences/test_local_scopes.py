"""PreferencesManager serves read-only local scope files while server scopes are off."""
from __future__ import annotations

import pytest

from lightfall.ui.preferences.manager import PreferencesManager
from tests.ui.preferences.test_manager_identity import FakeCM


@pytest.fixture
def pm(qapp, monkeypatch, tmp_path):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    # device_bcs_* are beamline/branch/user keys; a facility file may not set them.
    (tmp_path / "facility.yaml").write_text(
        "settings:\n  tiled_url: https://facility\n  device_bcs_port: 1234\n", encoding="utf-8")
    (tmp_path / "branch-7.0.1.1.yaml").write_text(
        "settings:\n  device_bcs_host: bcs011\n  device_bcs_port: 5577\n"
        "locks:\n  device_bcs_port: site standard\n", encoding="utf-8")
    PreferencesManager.reset()
    manager = PreferencesManager.get_instance()
    manager.set_config_manager(FakeCM("branch:7.0.1.1", device_bcs_host="mine",
                                      device_bcs_port=1))
    assert manager.load_local_scopes(tmp_path) is True
    yield manager
    PreferencesManager.reset()


def test_file_values_apply_and_yaml_overrides(pm):
    assert pm.get("tiled_url") == "https://facility"
    assert pm.get("device_bcs_host") == "mine"          # user's YAML wins over the branch file


def test_lock_beats_user_yaml(pm):
    assert pm.get("device_bcs_port") == 5577


def test_write_goes_to_yaml_and_reports_effective_value(pm):
    seen = []
    pm.subscribe("device_bcs_port", seen.append)
    pm.set("device_bcs_port", 9)
    assert pm._config_manager.data["preferences.device_bcs_port"] == 9
    assert seen == [5577]                                 # still locked


def test_unmentioned_keys_untouched(pm):
    pm.set("theme", "dark")
    assert pm.get("theme") == "dark"


def test_disabled_by_env(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("LIGHTFALL_SCOPES_DISABLED", "1")
    (tmp_path / "facility.yaml").write_text("settings:\n  tiled_url: x\n", encoding="utf-8")
    PreferencesManager.reset()
    try:
        assert PreferencesManager.get_instance().load_local_scopes(tmp_path) is False
    finally:
        PreferencesManager.reset()
