"""Monitors and feeds from the scoped, item-merged ``monitors.*`` / ``monitor_feeds.*`` settings."""
from __future__ import annotations

import pytest

from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin
from lightfall.monitor.registry import (
    FEED_INTERVALS_KEY,
    FEED_SEVERITY_KEY,
    FEEDS_KEY,
    MIGRATED_PREF,
    MONITORS_KEY,
    MonitorRegistry,
    advisor_floor,
    disabled_feed_names,
    feed_interval_overrides,
    migrate_monitor_prefs,
    start_monitor_migration,
)
from tests.plugins.test_plugin_enablement import BL, ChainServer, pm, serve  # noqa: F401
from tests.ui.preferences.test_manager_identity import FakeCM

KEYS = (MONITORS_KEY, FEEDS_KEY, FEED_INTERVALS_KEY, FEED_SEVERITY_KEY)


def _feed(name):
    return type(f"F_{name}", (MonitorFeed,), {
        "name": name, "evaluate": lambda self, ctx, window, prior: None})()


def _monitor(name, default=True):
    return type(f"M_{name}", (MonitorPlugin,), {
        "name": property(lambda self: name),
        "description": property(lambda self: name),
        "enabled_by_default": property(lambda self: default),
        "create_feeds": lambda self: [_feed(f"{name}_feed")],
    })()


@pytest.fixture
def registry():
    MonitorRegistry.reset_instance()
    reg = MonitorRegistry.get_instance()
    for m in (_monitor("alpha"), _monitor("beta"), _monitor("gamma", default=False)):
        reg.register(m)
    yield reg
    MonitorRegistry.reset_instance()


@pytest.fixture
def specs():
    from lightfall.scopes.keys import register_client_specs
    from lightfall.ui.preferences.monitor_settings import MonitorSettingsPlugin

    register_client_specs(MonitorSettingsPlugin().specs())  # as the page does at startup


def _names(reg):
    return {p.name for p in reg.enabled_plugins()}


def _legacy(**extra):
    return {
        "disabled_monitor_plugins": ["alpha", "both"],
        "forced_enabled_monitor_plugins": ["gamma", "both"],
        "disabled_monitor_feeds": ["f1"],
        "monitor_feed_intervals": {"f1": 5, "f2": 20.0, "bad0": 0, "badstr": "7", "badbool": True},
        "monitor_feed_advisor_severity": {"f1": "warn", "f2": "critical", "loud": "loud"},
        **extra,
    }


EXPECTED = {
    MONITORS_KEY: {"gamma": True, "both": False, "alpha": False},  # in both lists: off, as before
    FEEDS_KEY: {"f1": False},
    FEED_INTERVALS_KEY: {"f1": 5, "f2": 20},  # invalid intervals dropped
    FEED_SEVERITY_KEY: {"f1": "warn", "f2": "critical"},
}


# ── Readers ──────────────────────────────────────────────────────────────


def test_defaults_when_no_layer_mentions_anything(pm, registry):  # noqa: F811
    pm.set_config_manager(FakeCM(None))
    assert _names(registry) == {"alpha", "beta"}
    assert disabled_feed_names() == set()
    assert feed_interval_overrides() == {}
    assert advisor_floor("alpha_feed") == "info"


def test_readers_use_the_resolved_maps(pm, registry):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **{
        MONITORS_KEY: {"alpha": False, "gamma": True},
        FEEDS_KEY: {"f1": False, "f2": True},
        FEED_INTERVALS_KEY: {"f1": 12, "zero": 0, "neg": -3, "flag": True, "text": "9"},
        FEED_SEVERITY_KEY: {"f1": "critical", "odd": "loud"},
    }))
    assert _names(registry) == {"beta", "gamma"}
    assert [f.name for f in registry.enabled_feeds()] == ["beta_feed", "gamma_feed"]
    assert disabled_feed_names() == {"f1"}
    assert feed_interval_overrides() == {"f1": 12.0}  # stored invalid values are ignored
    assert advisor_floor("f1") == "critical"
    assert advisor_floor("odd") == "info"
    assert advisor_floor("other") == "info"


def test_old_keys_are_not_read(pm, registry):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **_legacy(), **{MIGRATED_PREF: ["local"]}))
    assert _names(registry) == {"alpha", "beta"}
    assert disabled_feed_names() == set()
    assert feed_interval_overrides() == {}
    assert advisor_floor("f1") == "info"


def test_scoped_overrides_decide(pm, serve, registry, specs):  # noqa: F811
    pm.set_config_manager(FakeCM(BL))
    server = ChainServer("u1")
    server.rows[(BL, MONITORS_KEY)] = ({"beta": False, "gamma": True}, 1)
    server.rows[(BL, FEED_INTERVALS_KEY)] = ({"f1": 90, "f2": 30}, 1)
    server.rows[(BL, FEED_SEVERITY_KEY)] = ({"f1": "warn"}, 1)
    server.rows[("user:u1", MONITORS_KEY)] = ({"gamma": False}, 1)
    server.rows[("user:u1", FEED_INTERVALS_KEY)] = ({"f1": 10}, 1)
    serve(server, "u1")
    assert _names(registry) == {"alpha"}  # beamline turns beta off; the user's own item wins
    assert feed_interval_overrides() == {"f1": 10.0, "f2": 30.0}
    assert advisor_floor("f1") == "warn"


# ── Migration ────────────────────────────────────────────────────────────


def test_migration_once(pm, registry):  # noqa: F811
    cm = FakeCM(None, **_legacy())
    pm.set_config_manager(cm)
    assert migrate_monitor_prefs(pm) is True
    for key in KEYS:
        assert pm.layer_value(key, "local") == EXPECTED[key], key
    assert _names(registry) == {"beta", "gamma"}
    assert disabled_feed_names() == {"f1"}
    assert cm.data["preferences.disabled_monitor_plugins"] == ["alpha", "both"]  # left untouched
    assert pm.get(MIGRATED_PREF) == ["local"]

    cm.data["preferences.disabled_monitor_plugins"] = ["beta"]  # not read any more
    before = dict(cm.data)
    assert migrate_monitor_prefs(pm) is False
    assert cm.data == before
    assert _names(registry) == {"beta", "gamma"}


def test_migration_keeps_values_already_set(pm):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **_legacy(**{
        MONITORS_KEY: {"alpha": True}, FEED_INTERVALS_KEY: {"f1": 99}})))
    migrate_monitor_prefs(pm)
    assert pm.layer_value(MONITORS_KEY, "local") == {"alpha": True, "gamma": True, "both": False}
    assert pm.layer_value(FEED_INTERVALS_KEY, "local") == {"f1": 99, "f2": 20}


def test_migration_fresh_install_writes_nothing_but_the_marker(pm):  # noqa: F811
    cm = FakeCM(None)
    pm.set_config_manager(cm)
    assert migrate_monitor_prefs(pm) is True
    assert not [k for k in cm.data if k.startswith(("preferences.monitors", "preferences.monitor_feeds"))]
    assert pm.get(MIGRATED_PREF) == ["local"]


def test_startup_migration_feeds_the_readers(pm, registry):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **_legacy()))
    start_monitor_migration(pm)
    assert _names(registry) == {"beta", "gamma"}
    assert feed_interval_overrides() == {"f1": 5.0, "f2": 20.0}
    assert advisor_floor("f2") == "critical"


def test_migration_in_server_mode_writes_the_user_scope(pm, serve, specs):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **_legacy()))
    server = ChainServer("u1")
    serve(server, "u1", bind="facility")
    assert migrate_monitor_prefs(pm) is True
    for key in KEYS:
        assert server.rows[("user:u1", key)][0] == EXPECTED[key], key
    assert pm.get(MIGRATED_PREF) == ["user:u1"]
    calls = len(server.calls)
    assert migrate_monitor_prefs(pm) is False
    assert len(server.calls) == calls


def test_startup_then_online_migrates_each_target_once(pm, serve, specs):  # noqa: F811
    cm = FakeCM(None, **_legacy())
    pm.set_config_manager(cm)
    start_monitor_migration(pm)
    assert cm.data["preferences.monitors.enabled"] == EXPECTED[MONITORS_KEY]
    server = serve(ChainServer("u1"), "u1", bind="facility")
    assert server.rows[("user:u1", MONITORS_KEY)][0] == EXPECTED[MONITORS_KEY]
    assert sorted(pm.get(MIGRATED_PREF)) == ["local", "user:u1"]
    start_monitor_migration(pm)
    assert len([c for c in server.calls if c[0] == "put" and c[2] == MONITORS_KEY]) == 1


def test_offline_scopes_do_not_migrate_the_user_scope(pm, serve):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **_legacy()))
    server = serve(ChainServer("u1"), "u1", bind="facility")
    pm.deactivate_scopes()
    server.down = True
    serve(server, "u1", bind="facility")
    assert migrate_monitor_prefs(pm) is False
    assert pm.get(MIGRATED_PREF) is None


def test_bound_console_keeps_its_lists_in_the_console_layer(pm, serve, registry, specs):  # noqa: F811
    cm = FakeCM(BL, **_legacy())
    pm.set_config_manager(cm)
    start_monitor_migration(pm)  # before login: this console's YAML
    assert cm.data["preferences.monitor_feeds.interval"] == EXPECTED[FEED_INTERVALS_KEY]
    server = serve(ChainServer("u2"), "u2", bind=BL)  # another user logs in here
    assert migrate_monitor_prefs(pm) is False
    assert not [c for c in server.calls if c[0] == "put" and c[2] in KEYS]
    assert pm.get(MIGRATED_PREF) == ["local"]
    # the console's choices apply via its local layer
    assert _names(registry) == {"beta", "gamma"}
    assert feed_interval_overrides() == {"f1": 5.0, "f2": 20.0}
    assert advisor_floor("f1") == "warn"


def test_yaml_upload_at_first_login_has_no_junk_monitor_keys():
    """The YAML's nested ``monitors:`` / ``monitor_feeds:`` groups flatten to the dotted keys."""
    from lightfall.scopes.keys import client_registry, flatten_preferences
    from lightfall.scopes.ops import migrate_user_prefs
    from tests.scopes.test_ops import FakeServer

    server = FakeServer()
    yaml = flatten_preferences({
        "monitors": {"enabled": {"alpha": False}},
        "monitor_feeds": {"enabled": {"f1": False}, "interval": {"f1": 5}, "severity": {"f1": "warn"}},
        "theme": "dark",
    })
    assert set(yaml) == {*KEYS, "theme"}
    assert migrate_user_prefs(server, "user:u1", yaml, client_registry()) == sorted({*KEYS, "theme"})
    assert ("user:u1", "monitors") not in server.rows
    assert ("user:u1", "monitor_feeds") not in server.rows


def test_scheduler_and_service_read_the_new_keys(pm):  # noqa: F811
    from lightfall.monitor.scheduler import _read_feed_config_prefs
    from lightfall.monitor.service import MonitorService

    pm.set_config_manager(FakeCM(None, **_legacy(**{
        FEEDS_KEY: {"f2": False}, FEED_INTERVALS_KEY: {"f2": 8}, FEED_SEVERITY_KEY: {"f2": "warn"},
        MIGRATED_PREF: ["local"]})))
    assert _read_feed_config_prefs() == ({"f2"}, {"f2": 8.0})
    service = MonitorService.__new__(MonitorService)  # the floor lookup needs no scheduler
    assert service._advisor_floor_for("f2") == "warn"
    assert service._advisor_floor_for("f1") == "info"  # the old dict is not read
