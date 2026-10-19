from __future__ import annotations

from lightfall.ui.preferences.manager import PreferencesManager
from tests.ui.preferences.test_manager_identity import FakeCM
from tests.ui.preferences.test_manager_scopes import pm  # noqa: F401 - fixture


def test_off_by_default(qapp):
    PreferencesManager.reset()
    try:
        assert PreferencesManager.get_instance().scope_info.mode == "off"
    finally:
        PreferencesManager.reset()


def test_server_mode_reports_chain_then_off(pm):  # noqa: F811
    manager, _server = pm
    seen = []
    manager.scopes_changed.connect(seen.append)
    manager.activate_scopes("u1", "beamline:7.0.1")
    assert manager.scope_info.mode == "server"
    assert manager.scope_info.chain == ("facility", "beamline:7.0.1", "user:u1")
    assert seen[-1].roles["beamline:7.0.1"] == "operator"
    manager.deactivate_scopes()
    assert manager.scope_info.mode == "off" and seen[-1].mode == "off"


def test_local_mode(qapp, monkeypatch, tmp_path):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    (tmp_path / "facility.yaml").write_text("settings:\n  tiled_url: x\n", encoding="utf-8")
    PreferencesManager.reset()
    try:
        manager = PreferencesManager.get_instance()
        manager.set_config_manager(FakeCM("beamline:7.0.1"))
        manager.load_local_scopes(tmp_path)
        assert manager.scope_info.mode == "local"
        assert manager.scope_info.chain == ("facility", "beamline:7.0.1")
    finally:
        PreferencesManager.reset()
