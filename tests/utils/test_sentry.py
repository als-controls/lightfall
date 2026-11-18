"""Tests for opt-in Sentry telemetry (lightfall.utils.sentry).

Telemetry must activate only when a DSN is explicitly configured (SENTRY_DSN
env var or 'telemetry_dsn' preference, env wins) and all reporting helpers
must no-op while inactive.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.utils import sentry


@pytest.fixture(autouse=True)
def _reset_sentry_state(monkeypatch):
    """Each test starts uninitialized with no ambient DSN configuration."""
    monkeypatch.setattr(sentry, "_initialized", False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(sentry, "_get_preference_dsn", lambda: None)


@pytest.fixture
def sentry_init(monkeypatch):
    """Mock out sentry_sdk.init so no real client is created."""
    import sentry_sdk

    mock = MagicMock()
    monkeypatch.setattr(sentry_sdk, "init", mock)
    return mock


def test_no_dsn_disables_telemetry(sentry_init):
    assert sentry.init_sentry(proxy_url="") is False
    sentry_init.assert_not_called()
    assert sentry._initialized is False


def test_env_dsn_activates_telemetry(monkeypatch, sentry_init):
    monkeypatch.setenv("SENTRY_DSN", "http://key@example.invalid/1")

    assert sentry.init_sentry(proxy_url="") is True

    sentry_init.assert_called_once()
    assert sentry_init.call_args.kwargs["dsn"] == "http://key@example.invalid/1"


def test_preference_dsn_activates_telemetry(monkeypatch, sentry_init):
    monkeypatch.setattr(
        sentry, "_get_preference_dsn", lambda: "http://pref@example.invalid/2"
    )

    assert sentry.init_sentry(proxy_url="") is True

    assert sentry_init.call_args.kwargs["dsn"] == "http://pref@example.invalid/2"


def test_env_dsn_wins_over_preference(monkeypatch, sentry_init):
    monkeypatch.setenv("SENTRY_DSN", "http://env@example.invalid/1")
    monkeypatch.setattr(
        sentry, "_get_preference_dsn", lambda: "http://pref@example.invalid/2"
    )

    assert sentry.init_sentry(proxy_url="") is True

    assert sentry_init.call_args.kwargs["dsn"] == "http://env@example.invalid/1"


def test_explicit_empty_dsn_disables(monkeypatch, sentry_init):
    """dsn='' means explicitly disabled, even when the env var is set."""
    monkeypatch.setenv("SENTRY_DSN", "http://env@example.invalid/1")

    assert sentry.init_sentry(dsn="", proxy_url="") is False

    sentry_init.assert_not_called()


def test_set_user_noops_when_inactive(monkeypatch):
    import sentry_sdk

    set_user_mock = MagicMock()
    monkeypatch.setattr(sentry_sdk, "set_user", set_user_mock)

    sentry.set_user(user_id="ron", username="ron", roles=["staff"])

    set_user_mock.assert_not_called()


# --- GitHub token backstop -------------------------------------------------------

_GH_TOKEN = "github_pat_SENTRYSECRET"


class _HeldSession:
    def __init__(self, token):
        self.token = token

    def _held_token(self):
        if isinstance(self.token, Exception):
            raise self.token
        return self.token


def _event():
    return {
        "message": f"push failed {_GH_TOKEN}",
        "exception": {"values": [{"value": f"boom {_GH_TOKEN}", "stacktrace": {"frames": [
            {"vars": {"env": {"LIGHTFALL_GITHUB_TOKEN": _GH_TOKEN}, "n": 3,
                      "args": (f"x{_GH_TOKEN}y", None)}}]}}]},
        "breadcrumbs": {"values": [{"message": "ok"}]},
    }


def test_before_send_scrubs_held_github_token(monkeypatch):
    from lightfall.github import session as session_mod

    monkeypatch.setattr(session_mod, "_instance", _HeldSession(_GH_TOKEN))
    out = sentry._before_send(_event(), {})
    assert _GH_TOKEN not in repr(out)
    assert out["message"] == "push failed ***"
    frame_vars = out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert frame_vars["env"]["LIGHTFALL_GITHUB_TOKEN"] == "***"
    assert frame_vars["args"] == ("x***y", None) and frame_vars["n"] == 3


def test_before_send_does_not_create_github_session(monkeypatch):
    from lightfall.github import session as session_mod

    monkeypatch.setattr(session_mod, "_instance", None)
    called = []
    monkeypatch.setattr(session_mod, "get_github_session", lambda: called.append(1))
    event = _event()
    assert sentry._before_send(event, {}) is event
    assert called == [] and session_mod._instance is None


@pytest.mark.parametrize("token", ["", None, RuntimeError("held token unavailable")])
def test_before_send_scrub_never_raises(monkeypatch, token):
    from lightfall.github import session as session_mod

    monkeypatch.setattr(session_mod, "_instance", _HeldSession(token))
    event = _event()
    assert sentry._before_send(event, {}) is event
