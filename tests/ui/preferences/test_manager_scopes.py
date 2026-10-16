"""PreferencesManager routes through ScopedBackend while scopes are active."""
from __future__ import annotations

import pytest

from lightfall.ui.preferences.manager import PreferencesManager
from tests.ui.preferences.test_scoped_backend import Server, sync_run


@pytest.fixture
def pm(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    import lightfall.ui.preferences.scoped_backend as sb

    server = Server()

    def fake_build(local, *, identity, bind, user_preferences):
        from lightfall.scopes.cache import SnapshotCache
        from lightfall.scopes.journal import WriteJournal

        return sb.ScopedBackend(local, client=server, cache=SnapshotCache(tmp_path / "c"),
                                journal=WriteJournal(tmp_path / "j.json"), bind=bind,
                                identity=identity, user_preferences=user_preferences,
                                run=sync_run, notify=lambda *a: None, poll_ms=0)

    monkeypatch.setattr(sb, "build_scoped_backend", fake_build)
    PreferencesManager.reset()
    manager = PreferencesManager.get_instance()
    yield manager, server
    manager.deactivate_scopes()
    PreferencesManager.reset()


def test_disabled_by_env(qapp, monkeypatch):
    monkeypatch.setenv("LIGHTFALL_SCOPES_DISABLED", "1")
    PreferencesManager.reset()
    try:
        assert PreferencesManager.get_instance().activate_scopes("u1", "facility") is False
    finally:
        PreferencesManager.reset()


def test_activate_routes_reads_and_writes(pm):
    manager, server = pm
    server.rows[("beamline:7.0.1", "tiled_url")] = ("https://bl", 1)
    seen = []
    manager.subscribe("tiled_url", seen.append)
    assert manager.activate_scopes("u1", "beamline:7.0.1") is True
    assert manager.get("tiled_url") == "https://bl"
    assert seen == ["https://bl"]
    manager.set("theme", "dark")
    assert server.rows[("user:u1", "theme")][0] == "dark"


def test_local_only_keys_bypass_scopes(pm):
    manager, server = pm
    manager.activate_scopes("u1", "beamline:7.0.1")
    manager.set("proxy_host", "example")
    assert ("user:u1", "proxy_host") not in server.rows


def test_scope_argument_requires_active_scopes(pm):
    manager, _ = pm
    with pytest.raises(ValueError):
        manager.set("tiled_url", "x", scope="beamline:7.0.1")


def test_deactivate_restores_local_routing(pm):
    manager, server = pm
    server.rows[("beamline:7.0.1", "tiled_url")] = ("https://bl", 1)
    manager.activate_scopes("u1", "beamline:7.0.1")
    manager.deactivate_scopes()
    assert manager.scoped_backend is None
    assert manager.get("tiled_url") != "https://bl"


def test_relogin_as_same_user_keeps_the_backend(pm):
    # Re-login after a session expiry must not flicker values through the YAML.
    manager, server = pm
    server.rows[("beamline:7.0.1", "tiled_url")] = ("https://bl", 1)
    manager.activate_scopes("u1", "beamline:7.0.1")
    first = manager.scoped_backend
    seen = []
    manager.subscribe("tiled_url", seen.append)
    manager.activate_scopes("u1", "beamline:7.0.1")
    assert manager.scoped_backend is first
    assert seen == []
