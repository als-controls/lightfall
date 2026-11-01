"""Plugin enablement from the scoped, item-merged ``plugins.enabled`` setting."""
from __future__ import annotations

from dataclasses import replace

import pytest

from lightfall.auth.provider_registry import AuthProviderRegistry
from lightfall.plugins.enablement import (
    DEFAULT_DISABLED_PLUGIN_IDS,
    MIGRATED_PREF,
    PLUGINS_KEY,
    default_enabled,
    disabled_plugin_ids,
    migrate_disabled_plugins,
    plugin_enabled,
    start_plugin_migration,
)
from lightfall.plugins.errors import PluginStatus
from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.manifest import PluginEntry, PluginManifest
from lightfall.plugins.types import PluginType
from lightfall.ui.preferences.manager import PreferencesManager
from tests.ui.preferences.test_manager_identity import FakeCM
from tests.ui.preferences.test_scoped_backend import CHAIN, Deferred, Server, sync_run

LOCAL = "auth_provider:local"
MEMORY = "statusbar:memory_status"
BL = "beamline:7.0.1"


class ChainServer(Server):
    """A fake server whose chain ends at the given user's scope."""

    def __init__(self, user: str = "u1", **kw):
        super().__init__(**kw)
        self._chain = replace(CHAIN, chain=("facility", BL, f"user:{user}"),
                              roles={"facility": None, BL: "operator", f"user:{user}": "owner"})

    def chain(self, bind):
        super().chain(bind)  # honours .down
        return self._chain


@pytest.fixture
def pm(qapp, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    PreferencesManager.reset()
    manager = PreferencesManager.get_instance()
    yield manager
    manager.deactivate_scopes()
    PreferencesManager.reset()


@pytest.fixture
def serve(pm, tmp_path, monkeypatch):
    """Activate server scopes on ``pm`` against a fake server, synchronously."""
    import lightfall.ui.preferences.scoped_backend as sb

    def _serve(server, identity="u1", bind=BL, run=sync_run, mode="server"):
        def fake_build(local, *, identity, bind, user_preferences):
            from lightfall.scopes.cache import SnapshotCache
            from lightfall.scopes.journal import WriteJournal

            return sb.ScopedBackend(
                local, client=server, cache=SnapshotCache(tmp_path / f"c-{identity}"),
                journal=WriteJournal(tmp_path / f"j-{identity}.json"), bind=bind,
                identity=identity, user_preferences=user_preferences, run=run,
                notify=lambda *a: None, poll_ms=0)

        monkeypatch.setattr(sb, "build_scoped_backend", fake_build)
        assert pm.activate_scopes(identity, bind)
        assert pm.scope_info.mode == mode
        return server
    return _serve


# ── Review Focus 1: beamline off, user on ────────────────────────────────


def test_beamline_off_user_on(pm, serve):
    pm.set_config_manager(FakeCM(BL))
    server = ChainServer("u1")
    server.rows[(BL, PLUGINS_KEY)] = ({"panel:x": False}, 1)
    server.rows[("user:u1", PLUGINS_KEY)] = ({"panel:x": True}, 1)
    serve(server, "u1")
    assert plugin_enabled("panel:x") is True
    assert disabled_plugin_ids({"panel:x", "panel:y"}) == set()

    # Another user on the same beamline has no override: the beamline's Off applies.
    pm.deactivate_scopes()
    other = ChainServer("u2")
    other.rows[(BL, PLUGINS_KEY)] = ({"panel:x": False}, 1)
    serve(other, "u2")
    assert plugin_enabled("panel:x") is False
    assert disabled_plugin_ids({"panel:x", "panel:y"}) == {"panel:x"}


# ── Code defaults ────────────────────────────────────────────────────────


def test_default_disabled_off_unless_enabled(pm):
    assert set(DEFAULT_DISABLED_PLUGIN_IDS) == {LOCAL, MEMORY}
    assert default_enabled(LOCAL) is False and default_enabled("panel:x") is True
    pm.set_config_manager(FakeCM(None))
    assert plugin_enabled(LOCAL) is False
    assert plugin_enabled(MEMORY) is False
    assert plugin_enabled("panel:x") is True
    assert disabled_plugin_ids({LOCAL, MEMORY, "panel:x"}) == {LOCAL, MEMORY}

    pm.set_config_manager(FakeCM(None, **{PLUGINS_KEY: {LOCAL: True, "panel:x": False}}))
    assert plugin_enabled(LOCAL) is True
    assert plugin_enabled("panel:x") is False
    assert disabled_plugin_ids({LOCAL, MEMORY, "panel:x"}) == {MEMORY, "panel:x"}


def test_settings_page_for_plugins_is_never_disabled(pm):
    pm.set_config_manager(FakeCM(None, **{PLUGINS_KEY: {"settings:plugins": False}}))
    assert plugin_enabled("settings:plugins") is True
    assert disabled_plugin_ids({"settings:plugins"}) == set()


def test_enablement_without_preferences_uses_code_defaults(monkeypatch):
    def boom():
        raise RuntimeError("no preferences yet")

    monkeypatch.setattr(PreferencesManager, "get_instance", staticmethod(boom))
    assert plugin_enabled("panel:x") is True
    assert plugin_enabled(LOCAL) is False


# ── Review Focus 2: migration runs once ──────────────────────────────────


def _legacy(**extra):
    return {"disabled_plugins": ["a", MEMORY],
            "default_disabled_plugins_seeded": [LOCAL, MEMORY], **extra}


def test_migration_once(pm):
    cm = FakeCM(None, **_legacy())
    pm.set_config_manager(cm)
    assert migrate_disabled_plugins(pm) is True
    assert pm.layer_value(PLUGINS_KEY, "local") == {"a": False, LOCAL: True}
    assert plugin_enabled("a") is False and plugin_enabled(LOCAL) is True
    assert plugin_enabled(MEMORY) is False  # still off: its code default
    assert cm.data["preferences.disabled_plugins"] == ["a", MEMORY]  # left untouched
    assert pm.get(MIGRATED_PREF) == ["local"]

    # A second run changes nothing, even if the old key moved since.
    cm.data["preferences.disabled_plugins"] = ["b"]
    before = dict(cm.data)
    assert migrate_disabled_plugins(pm) is False
    assert cm.data == before


def test_migration_legacy_true_marker_means_local_was_seeded(pm):
    pm.set_config_manager(FakeCM(None, disabled_plugins=[],
                                 default_disabled_plugins_seeded=True))
    migrate_disabled_plugins(pm)
    # local was seeded and then re-enabled; memory_status was never seeded: code default.
    assert pm.layer_value(PLUGINS_KEY, "local") == {LOCAL: True}


def test_migration_fresh_install_writes_nothing_but_the_marker(pm):
    cm = FakeCM(None)
    pm.set_config_manager(cm)
    assert migrate_disabled_plugins(pm) is True
    assert "preferences.plugins.enabled" not in cm.data
    assert pm.get(MIGRATED_PREF) == ["local"]
    assert plugin_enabled(LOCAL) is False


def test_migration_skips_the_protected_settings_page(pm):
    pm.set_config_manager(FakeCM(None, disabled_plugins=["settings:plugins", "a"]))
    migrate_disabled_plugins(pm)
    assert pm.layer_value(PLUGINS_KEY, "local") == {"a": False}


def test_migration_keeps_values_already_set(pm):
    pm.set_config_manager(FakeCM(None, **_legacy(**{PLUGINS_KEY: {"a": True, "z": False}})))
    migrate_disabled_plugins(pm)
    assert pm.layer_value(PLUGINS_KEY, "local") == {"a": True, "z": False, LOCAL: True}


def test_migration_in_scope_file_mode_writes_the_console_yaml(pm, tmp_path):
    (tmp_path / "beamline-7.0.1.yaml").write_text(
        "settings:\n  plugins.enabled:\n    z: false\n", encoding="utf-8")
    cm = FakeCM(BL, **_legacy())
    pm.set_config_manager(cm)
    assert pm.load_local_scopes(tmp_path) is True
    assert pm.scope_info.mode == "local" and pm.default_scope() == "local"
    migrate_disabled_plugins(pm)
    assert cm.data["preferences.plugins.enabled"] == {"a": False, LOCAL: True}
    assert pm.get(PLUGINS_KEY) == {"z": False, "a": False, LOCAL: True}
    assert plugin_enabled("a") is False and plugin_enabled(LOCAL) is True


def test_migration_in_server_mode_writes_the_user_scope(pm, serve):
    cm = FakeCM(None, **_legacy())  # an unbound workstation
    pm.set_config_manager(cm)
    server = ChainServer("u1")
    server.rows[("user:u1", PLUGINS_KEY)] = ({"b": True}, 1)
    serve(server, "u1", bind="facility")
    assert pm.default_scope() == "user:u1"
    assert migrate_disabled_plugins(pm) is True
    assert server.rows[("user:u1", PLUGINS_KEY)][0] == {"a": False, LOCAL: True, "b": True}
    assert "preferences.plugins.enabled" not in cm.data
    assert plugin_enabled("a") is False and plugin_enabled(LOCAL) is True
    assert sorted(pm.get(MIGRATED_PREF)) == ["user:u1"]
    calls = len(server.calls)
    assert migrate_disabled_plugins(pm) is False
    assert len(server.calls) == calls


def test_startup_migrates_the_yaml_then_the_user_scope_once_online(pm, serve):
    cm = FakeCM(None, **_legacy())  # an unbound workstation
    pm.set_config_manager(cm)
    start_plugin_migration(pm)  # before login: this console's YAML
    assert cm.data["preferences.plugins.enabled"] == {"a": False, LOCAL: True}
    server = serve(ChainServer("u1"), "u1", bind="facility")  # online: the user's scope
    assert server.rows[("user:u1", PLUGINS_KEY)][0] == {"a": False, LOCAL: True}
    assert sorted(pm.get(MIGRATED_PREF)) == ["local", "user:u1"]
    start_plugin_migration(pm)  # a second start: nothing more
    assert [c for c in server.calls if c[2] == PLUGINS_KEY] == [
        ("put", "user:u1", PLUGINS_KEY, {"a": False, LOCAL: True}, None)]


def test_offline_scopes_do_not_migrate_the_user_scope(pm, serve):
    pm.set_config_manager(FakeCM(None, **_legacy()))
    server = serve(ChainServer("u1"), "u1", bind="facility")  # an earlier login: cached
    pm.deactivate_scopes()
    server.down = True
    serve(server, "u1", bind="facility")  # offline: resolved from the cache, may be stale
    assert migrate_disabled_plugins(pm) is False
    assert pm.get(MIGRATED_PREF) is None
    assert ("user:u1", PLUGINS_KEY) not in server.rows


def test_bound_console_keeps_its_list_in_the_console_layer(pm, serve):
    cm = FakeCM(BL, **_legacy())  # a beamline console
    pm.set_config_manager(cm)
    start_plugin_migration(pm)
    server = serve(ChainServer("u2"), "u2")
    server.rows[(BL, PLUGINS_KEY)] = ({"z": False}, 1)
    pm.scoped_backend.refresh()
    assert migrate_disabled_plugins(pm) is False
    assert not [c for c in server.calls if c[0] == "put" and c[2] == PLUGINS_KEY]
    assert pm.get(MIGRATED_PREF) == ["local"]
    # The console's Off still applies, through this console's layer.
    assert plugin_enabled("a") is False and plugin_enabled(LOCAL) is True
    assert plugin_enabled("z") is False
    assert pm.item_origins(PLUGINS_KEY) == {"z": BL, "a": "local", LOCAL: "local"}
    inherited = pm.inherited_items(PLUGINS_KEY, "user:u2")
    assert inherited["a"] == (False, "This console")
    assert inherited[LOCAL] == (True, "This console")
    assert inherited["z"] == (False, "Beamline 7.0.1")
    # A user's own value still wins over the console's, for that user.
    pm.set(PLUGINS_KEY, {"a": True}, scope="user:u2")
    assert plugin_enabled("a") is True


def test_cached_scopes_reach_the_post_login_wave_before_the_fetch(pm, serve):
    pm.set_config_manager(FakeCM(BL))
    server = ChainServer("u1")
    server.rows[(BL, PLUGINS_KEY)] = ({"panel:x": False}, 1)
    serve(server, "u1")  # last session: fills the snapshot cache
    pm.deactivate_scopes()
    loader = _loader()
    loader.load_manifest(_manifest("panel:x"))  # before login: nothing switches it off
    assert loader._registry.get("panel", "x").status == PluginStatus.QUEUED_LOAD

    server.rows[(BL, PLUGINS_KEY)] = ({"panel:x": True}, 2)  # changed since
    run = Deferred()
    serve(server, "u1", run=run)  # this login: the fetch is still queued
    assert run.jobs
    assert pm.get(PLUGINS_KEY) == {"panel:x": False}  # last session's resolution
    loader._apply_enablement()
    assert loader._registry.get("panel", "x").status == PluginStatus.DISABLED
    seen = []
    pm.subscribe(PLUGINS_KEY, seen.append)
    run.run_all()  # the fetch lands
    assert pm.get(PLUGINS_KEY) == {"panel:x": True}
    assert seen == [{"panel:x": True}]


def test_no_cache_means_nothing_resolved_until_the_fetch(pm, serve):
    pm.set_config_manager(FakeCM(BL))
    server = ChainServer("u1")
    server.rows[(BL, PLUGINS_KEY)] = ({"panel:x": False}, 1)
    run = Deferred()
    serve(server, "u1", run=run, mode="off")  # first login on this machine
    assert pm.get(PLUGINS_KEY) is None
    run.run_all()
    assert pm.get(PLUGINS_KEY) == {"panel:x": False}


# ── Runtime readers ──────────────────────────────────────────────────────


def _manifest(*names, preload=()):
    return PluginManifest(
        name="test", version="0", description="t",
        plugins=[PluginEntry(type_name=t, name=n, import_path="x:Y",
                             preload=f"{t}:{n}" in preload)
                 for t, n in (name.split(":") for name in names)])


def _loader():
    loader = PluginLoader()
    for type_name in ("panel", "statusbar", "settings"):
        loader.register_plugin_type(type_name, PluginType)
    return loader


def test_loader_skips_disabled_ids(pm):
    pm.set_config_manager(FakeCM(None, **{PLUGINS_KEY: {"panel:off": False,
                                                        "settings:plugins": False}}))
    loader = _loader()
    loader.load_manifest(_manifest("panel:off", "panel:on", MEMORY, "settings:plugins"))
    status = {i.unique_id: i.status for i in loader._registry.get_all()}
    assert status == {"panel:off": PluginStatus.DISABLED, "panel:on": PluginStatus.QUEUED_LOAD,
                      MEMORY: PluginStatus.DISABLED,
                      "settings:plugins": PluginStatus.QUEUED_LOAD}
    assert {i.unique_id for i in loader._load_queue} == {"panel:on", "settings:plugins"}


def test_post_login_wave_rereads_enablement(pm):
    cm = FakeCM(None, **{PLUGINS_KEY: {"panel:a": False}})
    pm.set_config_manager(cm)
    loader = _loader()
    loader.load_manifest(_manifest("panel:a", "panel:b", "panel:early", preload=("panel:early",)))
    # Scopes resolve after login: a is now on, b and the preloaded plugin off.
    cm.data["preferences.plugins.enabled"] = {"panel:b": False, "panel:early": False}
    loader._apply_enablement()
    status = {i.unique_id: i.status for i in loader._registry.get_all()}
    assert status["panel:a"] == PluginStatus.QUEUED_LOAD
    assert status["panel:b"] == PluginStatus.DISABLED
    assert status["panel:early"] == PluginStatus.QUEUED_LOAD  # preload: next start
    assert [i.unique_id for i in loader._load_queue] == ["panel:early", "panel:a"]


def test_builtin_auth_respects_enablement(pm):
    from lightfall.auth.providers.builtin_plugins import register_builtin_auth_plugins

    def names(**prefs):
        pm.set_config_manager(FakeCM(None, **prefs))
        AuthProviderRegistry.reset()
        reg = AuthProviderRegistry.get_instance()
        register_builtin_auth_plugins(reg, config=None, include_pam=False)
        out = set(reg.get_names())
        AuthProviderRegistry.reset()
        return out

    assert names() == {"keycloak"}  # local ships off
    assert names(**{PLUGINS_KEY: {LOCAL: True}}) == {"keycloak", "local"}
    assert names(**{PLUGINS_KEY: {"auth_provider:keycloak": False}}) == set()


def test_yaml_upload_at_first_login_leaves_plugins_enabled_alone():
    # ScopedBackend's YAML -> user scope upload skips beamline-capable keys, so only
    # migrate_disabled_plugins writes plugins.enabled at the user's scope.
    from lightfall.scopes.keys import client_registry, flatten_preferences
    from lightfall.scopes.ops import migrate_user_prefs
    from tests.scopes.test_ops import FakeServer

    server = FakeServer()
    yaml = flatten_preferences({"plugins": {"enabled": {"a": False}}, "theme": "dark"})
    assert migrate_user_prefs(server, "user:u1", yaml, client_registry()) == ["theme"]
    assert ("user:u1", PLUGINS_KEY) not in server.rows
