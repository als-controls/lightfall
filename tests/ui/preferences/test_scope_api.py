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


def test_local_file_mode_layer_values_are_per_layer(local_pm):
    m = local_pm
    # This console holds nothing yet: the file value must not show as its own.
    assert m.layer_value("tiled_url", "local") is None
    assert m.layer_value("tiled_url", "facility") == "https://facility"
    assert m.layer_value("tiled_url", "branch:7.0.1.1") is None
    assert m.layer_value("device_bcs_port", "branch:7.0.1.1") == 5577
    m.set("tiled_url", "https://mine", scope="local")
    assert m.layer_value("tiled_url", "local") == "https://mine"
    assert m.layer_value("tiled_url", "facility") == "https://facility"
    m.remove("tiled_url", scope="local")
    assert m.layer_value("tiled_url", "local") is None
    assert m.get("tiled_url") == "https://facility"
    m.set("theme", "dark", scope="local")  # a key no scope file mentions
    assert m.layer_value("theme", "local") == "dark"
    assert m.layer_value("theme", "facility") is None


def test_off_mode_layer_value_is_the_console_yaml(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    PreferencesManager.reset()
    try:
        m = PreferencesManager.get_instance()
        m.set_config_manager(FakeCM("branch:7.0.1.1"))
        assert m.load_local_scopes(tmp_path) is False
        assert m.layer_value("theme", "local") is None
        m.set("theme", "dark", scope="local")
        assert m.layer_value("theme", "local") == "dark"
        assert m.layer_value("theme", "facility") is None
    finally:
        PreferencesManager.reset()


def test_needs_approval_only_for_page_declared_scopes(pm):  # noqa: F811
    from lightfall_settings import SettingSpec

    from lightfall.scopes.keys import register_client_specs

    register_client_specs([SettingSpec(
        key="approval.demo", scopes=frozenset({"user", "branch", "beamline"}),
        operator_writable=True)])
    manager, _ = pm
    assert not manager.needs_approval("approval.demo", "beamline:7.0.1")  # not logged in
    _login(manager)
    assert manager.needs_approval("approval.demo", "beamline:7.0.1")
    assert manager.needs_approval("approval.demo", "branch:7.0.1.1")
    assert not manager.needs_approval("approval.demo", "user:u1")
    assert not manager.needs_approval("approval.demo", "local")
    assert not manager.needs_approval("approval.demo", "facility")  # not allowed there
    assert not manager.needs_approval("theme", "beamline:7.0.1")    # base spec allows it
    approved = ({"key": "approval.demo", "type": "str", "scopes": ["beamline", "user"],
                 "operator_writable": True},)
    tsb.CHAIN = tsb.ChainInfo(**{**tsb.CHAIN.__dict__, "specs": approved})
    manager.scoped_backend.refresh()
    assert manager.scoped_backend.registry.get("approval.demo").allows("beamline")
    assert not manager.needs_approval("approval.demo", "beamline:7.0.1")


def test_needs_approval_false_in_local_file_mode(local_pm):
    assert not local_pm.needs_approval("tiled_url", "facility")
    assert not local_pm.needs_approval("theme", "local")


# Task 5: identity keys follow the console's binding ---------------------------

_IDENTITY = ("ipc_topic_prefix", "tiled_beamline")


def qapp_process():
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()


def _login_unbound(manager):
    tsb.CHAIN = tsb.ChainInfo(chain=("facility", "user:u1"),
                              roles={"facility": None, "user:u1": "owner"},
                              entitled=(), facility_label="ALS")
    assert manager.activate_scopes("u1", "facility")


def test_identity_keys_writable_at_local_only_when_unbound(pm):  # noqa: F811
    manager, _ = pm
    cm = FakeCM("facility")
    manager.set_config_manager(cm)
    for key in _IDENTITY:  # off mode
        assert manager.allowed_at(key, "local") and manager.writable_at(key, "local")
    _login_unbound(manager)
    for key in _IDENTITY:
        assert manager.allowed_at(key, "local"), key
        assert manager.writable_at(key, "local"), key
        assert not manager.writable_at(key, "user:u1"), key
    manager.set("ipc_topic_prefix", "dev.me", scope="local")
    assert cm.data["preferences.ipc_topic_prefix"] == "dev.me"
    assert manager.get("ipc_topic_prefix", "x") == "dev.me"
    assert manager.layer_value("ipc_topic_prefix", "local") == "dev.me"
    manager.remove("ipc_topic_prefix", scope="local")
    assert manager.get("ipc_topic_prefix", "x") == "x"
    manager.deactivate_scopes()

    cm.model.site.bind = "branch:7.0.1.1"
    _login(manager, role="maintainer")
    for key in _IDENTITY:
        assert manager.allowed_at(key, "local"), key
        assert not manager.writable_at(key, "local"), key
        assert manager.allowed_at(key, "branch:7.0.1.1"), key
        assert manager.writable_at(key, "branch:7.0.1.1"), key
    with pytest.raises(ValueError, match="binding"):
        manager.set("ipc_topic_prefix", "dev.me", scope="local")


def test_server_bound_console_prefers_scope_value_over_derived(pm):  # noqa: F811
    manager, server = pm
    manager.set_config_manager(FakeCM("branch:7.0.1.1", ipc_topic_prefix="dev.me"))
    _login(manager)
    assert manager.get("ipc_topic_prefix", "x") == "als.7011"  # console value ignored
    assert manager.get("tiled_beamline") == "7.0.1.1"
    seen = []
    manager.subscribe("ipc_topic_prefix", seen.append)
    server.rows[("branch:7.0.1.1", "ipc_topic_prefix")] = ("als.custom", 1)
    manager.scoped_backend.refresh()
    assert manager.get("ipc_topic_prefix", "x") == "als.custom"
    del server.rows[("branch:7.0.1.1", "ipc_topic_prefix")]
    manager.scoped_backend.refresh()
    assert manager.get("ipc_topic_prefix", "x") == "als.7011"
    qapp_process()
    assert seen == ["als.custom", "als.7011"]  # subscribers see the resolved value


def test_facility_admin_can_write_at_facility(pm):  # noqa: F811
    manager, _ = pm
    tsb.CHAIN = tsb.ChainInfo(
        chain=("facility", "beamline:7.0.1", "user:u1"),
        roles={"facility": "admin", "beamline:7.0.1": "admin", "user:u1": "owner"},
        entitled=("beamline:7.0.1",), facility_label="ALS")
    assert manager.activate_scopes("u1", "beamline:7.0.1")
    facility = next(c for c in manager.scope_choices() if c.kind == "facility")
    assert facility.role == "admin" and facility.read_only_reason == ""
    assert "facility" in manager.write_targets("tiled_url")
    assert manager.writable_at("tiled_url", "facility")
    assert not manager.writable_at("theme", "facility")  # theme's spec doesn't allow facility


def test_facility_viewer_without_grant_stays_read_only(pm):  # noqa: F811
    manager, _ = pm
    _login(manager)
    facility = next(c for c in manager.scope_choices() if c.kind == "facility")
    assert "facility admins" in facility.read_only_reason
    assert not manager.writable_at("tiled_url", "facility")


def test_plugin_console_keys_are_console_only(pm):  # noqa: F811
    from lightfall.plugins.settings_plugin import SettingsPlugin

    class _Page(SettingsPlugin):
        name = "console_keys_page"

        def console_keys(self):
            return ("vibe.device_id",)

    manager, _ = pm
    _login(manager)
    assert manager.allowed_at("vibe.device_id", "user:u1")
    _Page()
    assert not manager.allowed_at("vibe.device_id", "user:u1")
    assert not manager.allowed_at("vibe.device_id", "beamline:7.0.1")
    assert manager.allowed_at("vibe.device_id", "local")
    assert manager.writable_at("vibe.device_id", "local")
    assert not manager.writable_at("vibe.device_id", "user:u1")
