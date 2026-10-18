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
    monkeypatch.setattr(tiled, "connect_async", lambda: record["connects"].append(1))
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


def test_reconfigure_reports_errors_instead_of_raising(prefs, cfg, calls):
    from lightfall.setup.services import reconfigure_services

    prefs.set("tiled_auth_mode", "bogus")
    errors = reconfigure_services(cfg)
    assert len(errors) == 1 and errors[0].startswith("Tiled:")
