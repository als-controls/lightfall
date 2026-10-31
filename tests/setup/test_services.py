"""reconfigure_services(): re-apply auth and Tiled from the current config."""
from __future__ import annotations

import pytest

from lightfall.auth.session import Session, SessionManager, User


@pytest.fixture
def session(qapp):
    from lightfall.auth.provider_registry import AuthProviderRegistry

    AuthProviderRegistry.reset()
    SessionManager.reset()
    yield SessionManager.get_instance()
    SessionManager.reset()
    AuthProviderRegistry.reset()


@pytest.fixture
def tiled(qapp):
    from lightfall.services.tiled_service import TiledService

    TiledService.reset()
    yield TiledService.get_instance()
    TiledService.reset()


@pytest.fixture
def calls(session, tiled, monkeypatch):
    record = {"providers": [], "configures": [], "connects": []}
    monkeypatch.setattr(session, "set_provider",
                        lambda p: record["providers"].append(type(p).__name__))
    real_configure = tiled.configure

    def configure(**kwargs):
        record["configures"].append(kwargs)
        real_configure(**kwargs)

    monkeypatch.setattr(tiled, "configure", configure)
    def connect_async():
        # Like the real one: the state is CONNECTING as soon as the call returns.
        from lightfall.services.tiled_service import TiledConnectionState

        record["connects"].append(1)
        tiled._set_state(TiledConnectionState.CONNECTING, "test")

    monkeypatch.setattr(tiled, "connect_async", connect_async)
    return record


def test_reconfigure_is_idempotent(prefs, cfg, calls):
    from lightfall.setup.services import reconfigure_services

    prefs.set("tiled_enabled", True)
    prefs.set("tiled_url", "http://tiled.example:8000")
    prefs.set("tiled_auth_mode", "none")
    assert reconfigure_services(cfg) == []
    assert reconfigure_services(cfg) == []
    assert len(calls["providers"]) == 2 and calls["providers"][0] == calls["providers"][1]
    assert len(calls["configures"]) == 1
    assert calls["configures"][0]["url"] == "http://tiled.example:8000"
    assert calls["connects"] == [1]


def test_reconfigure_picks_up_a_new_tiled_url(prefs, cfg, calls):
    from lightfall.setup.services import reconfigure_services

    prefs.set("tiled_enabled", True)
    prefs.set("tiled_auth_mode", "none")
    prefs.set("tiled_url", "http://a.example")
    reconfigure_services(cfg)
    prefs.set("tiled_url", "http://b.example")
    reconfigure_services(cfg)
    assert [c["url"] for c in calls["configures"]] == ["http://a.example", "http://b.example"]


def test_reconfigure_keeps_provider_while_signed_in(prefs, cfg, session, calls):
    from lightfall.setup.services import reconfigure_services

    session.attach_session(Session(user=User(username="ron")))  # token=None: no key mint
    assert reconfigure_services(cfg) == []
    assert calls["providers"] == []
    assert session.is_authenticated


def test_reconfigure_while_signed_in_reconnects_keycloak_tiled(prefs, cfg, session, calls):
    from lightfall.setup.services import reconfigure_services

    session.attach_session(Session(user=User(username="ron")))
    prefs.set("tiled_enabled", True)
    prefs.set("tiled_url", "http://tiled.example")
    prefs.set("tiled_auth_mode", "keycloak")
    assert reconfigure_services(cfg) == []
    assert calls["connects"] == [1]


def test_keycloak_tiled_waits_for_login_when_signed_out(prefs, cfg, calls):
    from lightfall.setup.services import reconfigure_services

    prefs.set("tiled_enabled", True)
    prefs.set("tiled_url", "http://tiled.example")
    prefs.set("tiled_auth_mode", "keycloak")
    assert reconfigure_services(cfg) == []
    assert calls["connects"] == []


def test_reconfigure_reports_errors_instead_of_raising(prefs, cfg, tiled, calls, monkeypatch):
    from lightfall.setup.services import reconfigure_services

    def boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tiled, "configure", boom)
    prefs.set("tiled_enabled", True)
    prefs.set("tiled_url", "http://tiled.example:8000")
    errors = reconfigure_services(cfg)
    assert len(errors) == 1 and errors[0].startswith("Tiled:")


def _set_tiled_state(tiled, state):
    tiled._set_state(state, "test")


@pytest.mark.parametrize("state", ["ERROR", "DISCONNECTED"])
def test_rerun_retries_a_failed_tiled_connection(prefs, cfg, tiled, calls, state):
    from lightfall.services.tiled_service import TiledConnectionState
    from lightfall.setup.services import reconfigure_services

    prefs.set("tiled_enabled", True)
    prefs.set("tiled_url", "http://tiled.example")
    prefs.set("tiled_auth_mode", "none")
    reconfigure_services(cfg)
    assert calls["connects"] == [1]
    _set_tiled_state(tiled, TiledConnectionState[state])
    assert reconfigure_services(cfg) == []
    assert len(calls["configures"]) == 1  # config unchanged: not re-configured
    assert calls["connects"] == [1, 1]  # but the failed connection is retried


@pytest.mark.parametrize("state", ["CONNECTED", "CONNECTING"])
def test_rerun_leaves_a_live_tiled_connection_alone(prefs, cfg, tiled, calls, state):
    from lightfall.services.tiled_service import TiledConnectionState
    from lightfall.setup.services import reconfigure_services

    prefs.set("tiled_enabled", True)
    prefs.set("tiled_url", "http://tiled.example")
    prefs.set("tiled_auth_mode", "none")
    reconfigure_services(cfg)
    _set_tiled_state(tiled, TiledConnectionState[state])
    assert reconfigure_services(cfg) == []
    assert calls["connects"] == [1]


def test_rerun_does_not_retry_keycloak_tiled_while_signed_out(prefs, cfg, tiled, calls):
    from lightfall.services.tiled_service import TiledConnectionState
    from lightfall.setup.services import reconfigure_services

    prefs.set("tiled_enabled", True)
    prefs.set("tiled_url", "http://tiled.example")
    prefs.set("tiled_auth_mode", "keycloak")
    reconfigure_services(cfg)
    _set_tiled_state(tiled, TiledConnectionState.ERROR)
    assert reconfigure_services(cfg) == []
    assert calls["connects"] == []


def test_rerun_retries_keycloak_tiled_when_signed_in(prefs, cfg, session, tiled, calls):
    from lightfall.services.tiled_service import TiledConnectionState
    from lightfall.setup.services import reconfigure_services

    session.attach_session(Session(user=User(username="ron")))
    prefs.set("tiled_enabled", True)
    prefs.set("tiled_url", "http://tiled.example")
    prefs.set("tiled_auth_mode", "keycloak")
    reconfigure_services(cfg)
    _set_tiled_state(tiled, TiledConnectionState.ERROR)
    assert reconfigure_services(cfg) == []
    assert calls["connects"] == [1, 1]


def test_rerun_does_not_connect_a_disabled_tiled(prefs, cfg, tiled, calls):
    from lightfall.services.tiled_service import TiledConnectionState
    from lightfall.setup.services import reconfigure_services

    prefs.set("tiled_enabled", False)
    prefs.set("tiled_url", "http://tiled.example")
    reconfigure_services(cfg)
    _set_tiled_state(tiled, TiledConnectionState.ERROR)
    assert reconfigure_services(cfg) == []
    assert calls["connects"] == []


def test_configure_tiled_treats_an_invalid_auth_mode_as_auto_detect(prefs, cfg, calls):
    from lightfall.services.tiled_service import TiledAuthMode
    from lightfall.setup.services import configure_tiled

    prefs.set("tiled_enabled", True)
    prefs.set("tiled_url", "http://tiled.example:8000")
    prefs.set("tiled_api_key", "k")
    prefs.set("tiled_auth_mode", "oidc")
    configure_tiled(cfg)  # raised ValueError("'oidc' is not a valid TiledAuthMode") before
    assert calls["configures"][0]["auth_mode"] == TiledAuthMode.API_KEY


def test_watch_reapplies_tiled_when_a_pref_changes_after_startup(prefs, cfg, qapp, calls):
    """Scoped values (e.g. beamline-scope tiled_enabled) land after login as key changes."""
    from lightfall.setup.services import configure_tiled, watch_tiled_preferences

    configure_tiled(cfg)  # startup, before scopes: Tiled reads as disabled
    watch_tiled_preferences(cfg)
    assert not any(c["enabled"] for c in calls["configures"])

    prefs.set("tiled_auth_mode", "none")
    prefs.set("tiled_url", "http://tiled.example")
    prefs.set("tiled_enabled", True)
    qapp.processEvents()  # the three changes coalesce into one re-apply

    enabled = [c for c in calls["configures"] if c["enabled"]]
    assert len(enabled) == 1 and enabled[0]["url"] == "http://tiled.example"
    assert calls["connects"] == [1]
