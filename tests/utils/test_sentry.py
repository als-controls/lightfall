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
