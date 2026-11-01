"""Assistant tool enablement from the scoped, item-merged ``tools.enabled`` setting."""
from __future__ import annotations

import pytest

from lightfall.plugins.tool_plugin import ToolPlugin
from lightfall.ui.panels.claude.tool_registry import (
    MIGRATED_PREF,
    TOOLS_KEY,
    ToolRegistry,
    migrate_tool_lists,
    start_tool_migration,
)
from tests.plugins.test_plugin_enablement import BL, ChainServer, pm, serve  # noqa: F401
from tests.ui.preferences.test_manager_identity import FakeCM


def _tool(name, default=True):
    return type(f"T_{name}", (ToolPlugin,), {
        "name": property(lambda self: name),
        "description": property(lambda self: name),
        "enabled_by_default": property(lambda self: default),
    })()


@pytest.fixture
def registry():
    ToolRegistry.reset_instance()
    reg = ToolRegistry.get_instance()
    for t in (_tool("alpha"), _tool("beta"), _tool("gamma", default=False)):
        reg.register(t)
    yield reg
    ToolRegistry.reset_instance()


def _names(reg):
    return {p.name for p in reg.enabled_plugins()}


LEGACY_MAP = {"gamma": True, "both": False, "alpha": False}


def _legacy(**extra):
    return {"disabled_tool_plugins": ["alpha", "both"],
            "forced_enabled_tool_plugins": ["gamma", "both"], **extra}


def test_defaults_when_no_layer_mentions_a_tool(pm, registry):  # noqa: F811
    pm.set_config_manager(FakeCM(None))
    assert _names(registry) == {"alpha", "beta"}


def test_scoped_overrides_decide(pm, serve, registry):  # noqa: F811
    from lightfall.scopes.keys import register_client_specs
    from lightfall.ui.preferences.tool_settings import ClaudeToolsSettingsPlugin

    register_client_specs(ClaudeToolsSettingsPlugin().specs())  # as the page does at startup
    pm.set_config_manager(FakeCM(BL))
    server = ChainServer("u1")
    server.rows[(BL, TOOLS_KEY)] = ({"beta": False, "gamma": True}, 1)
    server.rows[("user:u1", TOOLS_KEY)] = ({"gamma": False, "alpha": False}, 1)
    serve(server, "u1")
    assert _names(registry) == set()  # beamline turns beta off; the user's own items win per item

    pm.deactivate_scopes()
    other = ChainServer("u2")
    other.rows[(BL, TOOLS_KEY)] = ({"beta": False, "gamma": True}, 1)
    serve(other, "u2")
    assert _names(registry) == {"alpha", "gamma"}  # another user sees the beamline's choices


def test_migration_once(pm, registry):  # noqa: F811
    cm = FakeCM(None, **_legacy())
    pm.set_config_manager(cm)
    assert migrate_tool_lists(pm) is True
    # a name in both lists is off, as the old registry had it
    assert pm.layer_value(TOOLS_KEY, "local") == LEGACY_MAP
    assert _names(registry) == {"beta", "gamma"}
    assert cm.data["preferences.disabled_tool_plugins"] == ["alpha", "both"]  # left untouched
    assert pm.get(MIGRATED_PREF) == ["local"]

    cm.data["preferences.disabled_tool_plugins"] = ["beta"]  # not read any more
    before = dict(cm.data)
    assert migrate_tool_lists(pm) is False
    assert cm.data == before
    assert _names(registry) == {"beta", "gamma"}


def test_migration_keeps_values_already_set(pm):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **_legacy(**{TOOLS_KEY: {"alpha": True, "z": False}})))
    migrate_tool_lists(pm)
    assert pm.layer_value(TOOLS_KEY, "local") == {
        "alpha": True, "z": False, "gamma": True, "both": False}


def test_migration_fresh_install_writes_nothing_but_the_marker(pm):  # noqa: F811
    cm = FakeCM(None)
    pm.set_config_manager(cm)
    assert migrate_tool_lists(pm) is True
    assert "preferences.tools.enabled" not in cm.data
    assert pm.get(MIGRATED_PREF) == ["local"]


def test_startup_migration_feeds_the_registry(pm, registry):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **_legacy()))
    start_tool_migration(pm)
    assert _names(registry) == {"beta", "gamma"}
    assert pm.get(MIGRATED_PREF) == ["local"]


def test_reading_enabled_plugins_alone_writes_nothing(pm, registry):  # noqa: F811
    cm = FakeCM(None)
    pm.set_config_manager(cm)
    before = dict(cm.data)
    assert _names(registry) == {"alpha", "beta"}
    assert cm.data == before  # only a converted allow-list triggers the migration there


def test_legacy_allow_list_goes_through_the_lists_into_the_map(pm, registry):  # noqa: F811
    """enabled_tool_plugins -> the two lists (as before) -> tools.enabled."""
    cm = FakeCM(None, enabled_tool_plugins=["beta", "gamma"])
    pm.set_config_manager(cm)
    assert _names(registry) == {"beta", "gamma"}  # first read converts, then migrates
    assert pm.layer_value(TOOLS_KEY, "local") == {"alpha": False, "gamma": True}
    assert "preferences.enabled_tool_plugins" not in cm.data


def test_allow_list_waits_for_an_empty_registry_without_marking(pm):  # noqa: F811
    ToolRegistry.reset_instance()  # nothing registered yet (startup)
    cm = FakeCM(None, enabled_tool_plugins=["beta"])
    pm.set_config_manager(cm)
    start_tool_migration(pm)
    assert pm.get(MIGRATED_PREF) is None
    assert "preferences.tools.enabled" not in cm.data
    ToolRegistry.reset_instance()


def test_migration_in_server_mode_writes_the_user_scope(pm, serve):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **_legacy()))
    server = ChainServer("u1")
    serve(server, "u1", bind="facility")
    assert migrate_tool_lists(pm) is True
    assert server.rows[("user:u1", TOOLS_KEY)][0] == LEGACY_MAP
    assert pm.get(MIGRATED_PREF) == ["user:u1"]
    calls = len(server.calls)
    assert migrate_tool_lists(pm) is False
    assert len(server.calls) == calls


def test_startup_then_online_migrates_each_target_once(pm, serve):  # noqa: F811
    cm = FakeCM(None, **_legacy())
    pm.set_config_manager(cm)
    start_tool_migration(pm)
    assert cm.data["preferences.tools.enabled"] == LEGACY_MAP
    server = serve(ChainServer("u1"), "u1", bind="facility")
    assert server.rows[("user:u1", TOOLS_KEY)][0] == LEGACY_MAP
    assert sorted(pm.get(MIGRATED_PREF)) == ["local", "user:u1"]
    start_tool_migration(pm)
    assert len([c for c in server.calls if c[2] == TOOLS_KEY]) == 1


def test_offline_scopes_do_not_migrate_the_user_scope(pm, serve):  # noqa: F811
    pm.set_config_manager(FakeCM(None, **_legacy()))
    server = serve(ChainServer("u1"), "u1", bind="facility")
    pm.deactivate_scopes()
    server.down = True
    serve(server, "u1", bind="facility")
    assert migrate_tool_lists(pm) is False
    assert pm.get(MIGRATED_PREF) is None


def test_bound_console_user_scope_still_migrates(pm, serve):  # noqa: F811
    """tools.enabled has no console layer: a bound console's user gets their own lists."""
    pm.set_config_manager(FakeCM(BL, **_legacy()))
    server = serve(ChainServer("u1"), "u1", bind=BL)
    assert migrate_tool_lists(pm) is True
    assert server.rows[("user:u1", TOOLS_KEY)][0]["alpha"] is False


def test_yaml_upload_at_first_login_has_no_junk_tools_key():
    """The YAML's nested ``tools:`` group flattens to ``tools.enabled``, never a ``tools`` key.

    tools.enabled is not a shared/deployment spec, so the first-login upload treats it as
    any user key: the console's value (migrated there from the old lists) lands in the
    user's still-empty scope, the same value migrate_tool_lists writes for that target.
    """
    from lightfall.scopes.keys import client_registry, flatten_preferences
    from lightfall.scopes.ops import migrate_user_prefs
    from tests.scopes.test_ops import FakeServer

    server = FakeServer()
    yaml = flatten_preferences({"tools": {"enabled": {"alpha": False}}, "theme": "dark"})
    assert set(yaml) == {"tools.enabled", "theme"}
    migrate_user_prefs(server, "user:u1", yaml, client_registry())
    assert ("user:u1", "tools") not in server.rows
