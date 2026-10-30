"""Scope API on PreferencesManager: choices, allowed/writable, locks, default scope."""
from __future__ import annotations

import pytest
from lightfall_settings import Lock

from lightfall.scopes.keys import client_registry
from lightfall.ui.preferences.manager import PreferencesManager
from tests.ui.preferences import test_scoped_backend as tsb
from tests.ui.preferences.test_manager_identity import FakeCM
from tests.ui.preferences.test_manager_scopes import pm  # noqa: F401


@pytest.fixture(autouse=True)
def _restore_chain():
    saved = tsb.CHAIN
    yield
    tsb.CHAIN = saved


def _login(manager, role="operator"):
    tsb.CHAIN = tsb.ChainInfo(
        chain=("facility", "beamline:7.0.1", "branch:7.0.1.1", "user:u1"),
        roles={"facility": None, "beamline:7.0.1": role, "branch:7.0.1.1": role, "user:u1": "owner"},
        entitled=("beamline:7.0.1",), facility_label="ALS")
    assert manager.activate_scopes("u1", "branch:7.0.1.1")


def test_choices_off_mode_is_console_only(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    PreferencesManager.reset()
    try:
        m = PreferencesManager.get_instance()
        m.deactivate_scopes()
        assert m.load_local_scopes(tmp_path) is False
        assert m.scope_info.mode == "off"
        (c,) = m.scope_choices()
        assert (c.scope, c.kind, c.label, c.read_only_reason) == ("local", "local", "This console", "")
        assert m.default_scope() == "local"
        assert m.writable_at("theme", "local")
        assert m.allowed_at("theme", "local")
    finally:
        PreferencesManager.reset()


@pytest.fixture
def local_pm(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    (tmp_path / "facility.yaml").write_text(
        "settings:\n  tiled_url: https://facility\n", encoding="utf-8")
    (tmp_path / "branch-7.0.1.1.yaml").write_text(
        "settings:\n  device_bcs_port: 5577\nlocks:\n  device_bcs_port: site standard\n",
        encoding="utf-8")
    PreferencesManager.reset()
    manager = PreferencesManager.get_instance()
    manager.set_config_manager(FakeCM("branch:7.0.1.1"))
    assert manager.load_local_scopes(tmp_path) is True
    yield manager
    PreferencesManager.reset()


def test_local_file_mode_choices_and_rules(local_pm):
    m = local_pm
    choices = m.scope_choices()
    assert [c.scope for c in choices] == ["local", "branch:7.0.1.1", "beamline:7.0.1", "facility"]
    assert choices[0].read_only_reason == ""
    assert {c.read_only_reason for c in choices[1:]} == {"Read-only: scope files on this console"}
    assert choices[3].label == "Facility \u2014 facility"
    assert m.default_scope() == "local"
    assert m.allowed_at("theme", "local")
    assert m.writable_at("theme", "local")
    assert not m.writable_at("theme", "branch:7.0.1.1")
    assert not m.writable_at("theme", "facility")


def test_local_file_mode_lock_from_resolution(local_pm):
    m = local_pm
    info = m.lock_at("device_bcs_port", "local")
    assert info is not None and info.locked_at == "branch:7.0.1.1"
    assert m.lock_at("device_bcs_port", "branch:7.0.1.1") is None
    assert m.lock_at("tiled_url", "local") is None


def test_choices_server_order_and_labels(pm):  # noqa: F811
    manager, _ = pm
    _login(manager)
    choices = manager.scope_choices()
    assert [c.scope for c in choices] == ["user:u1", "local", "branch:7.0.1.1", "beamline:7.0.1", "facility"]
    assert [c.label for c in choices[:4]] == [
        "User — u1", "This console", "Branch 7.0.1.1", "Beamline 7.0.1"]
    assert choices[2].read_only_reason == ""
    assert choices[4].label == "Facility \u2014 ALS"
    assert "facility admins" in choices[4].read_only_reason


def test_viewer_role_gives_read_only_reason(pm):  # noqa: F811
    manager, _ = pm
    _login(manager, role="viewer")
    by = {c.scope: c for c in manager.scope_choices()}
    assert by["beamline:7.0.1"].read_only_reason == "Read-only: you are a viewer here"
    assert by["user:u1"].read_only_reason == ""


def test_console_only_key_allowed_only_local(pm):  # noqa: F811
    manager, _ = pm
    _login(manager)
    assert manager.allowed_at("proxy_host", "local")
    assert not manager.allowed_at("proxy_host", "user:u1")
    assert not manager.allowed_at("proxy_host", "beamline:7.0.1")
    assert not manager.allowed_at("theme", "local")  # server mode: only LOCAL_LAYER_KEYS
    assert manager.allowed_at("device_backends", "local")


def test_writable_matches_write_targets(pm):  # noqa: F811
    manager, _ = pm
    _login(manager)
    choices = manager.scope_choices()
    for key in client_registry().keys():
        for c in choices:
            if manager.writable_at(key, c.scope):
                assert manager.allowed_at(key, c.scope), (key, c.scope)
    assert manager.writable_at("theme", "user:u1")
    assert not manager.writable_at("theme", "facility")


def test_lock_above_blocks_lock_here_does_not(pm):  # noqa: F811
    manager, server = pm
    server.rows[("beamline:7.0.1", "tiled_url")] = ("https://bl", 1)
    _login(manager)
    store = manager.scoped_backend.store
    store._locks["beamline:7.0.1"]["tiled_url"] = Lock(
        locked_by="ron", reason="frozen", locked_at="beamline:7.0.1")
    info = manager.lock_at("tiled_url", "user:u1")
    assert (info.locked_at, info.locked_by, info.reason) == ("beamline:7.0.1", "ron", "frozen")
    assert manager.lock_at("tiled_url", "local") is not None
    assert manager.lock_at("tiled_url", "branch:7.0.1.1") is not None
    assert manager.lock_at("tiled_url", "beamline:7.0.1") is None
    assert manager.lock_at("tiled_url", "facility") is None
    assert manager.lock_at("theme", "user:u1") is None


def test_default_scope_user_when_server(pm):  # noqa: F811
    manager, _ = pm
    assert manager.default_scope() == "local"
    _login(manager)
    assert manager.default_scope() == "user:u1"
