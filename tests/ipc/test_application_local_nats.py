"""Tests for local-NATS wiring in Application._create_ipc_service / _shutdown."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.core.application import LFApplication


def _app_without_init() -> LFApplication:
    return LFApplication.__new__(LFApplication)


def _patch_prefs(monkeypatch, values: dict):
    mock_prefs = MagicMock()
    mock_prefs.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
    import lightfall.ui.preferences.manager as mgr
    monkeypatch.setattr(mgr.PreferencesManager, "get_instance", lambda: mock_prefs)


def test_disabled_uses_site_url(qapp, monkeypatch):
    _patch_prefs(monkeypatch, {
        "ipc_nats_url": "nats://site:4222",
        "ipc_topic_prefix": "als.7011",
        "ipc_use_local_nats": False,
    })
    app = _app_without_init()
    svc = app._create_ipc_service(MagicMock())
    assert svc.nats_url == "nats://site:4222"
    assert app._local_nats is None


def test_enabled_starts_manager_and_uses_localhost(qapp, monkeypatch):
    _patch_prefs(monkeypatch, {
        "ipc_nats_url": "nats://site:4222",
        "ipc_topic_prefix": "als.7011",
        "ipc_use_local_nats": True,
        "ipc_local_nats_port": 4299,
    })
    fake_mgr = MagicMock()
    import lightfall.ipc.local_server as ls
    monkeypatch.setattr(ls, "LocalNatsServer", MagicMock(return_value=fake_mgr))

    app = _app_without_init()
    svc = app._create_ipc_service(MagicMock())
    assert svc.nats_url == "nats://127.0.0.1:4299"
    fake_mgr.start.assert_called_once()
    assert app._local_nats is fake_mgr


def test_enabled_start_failure_disables_ipc(qapp, monkeypatch):
    _patch_prefs(monkeypatch, {
        "ipc_nats_url": "nats://site:4222",
        "ipc_topic_prefix": "als.7011",
        "ipc_use_local_nats": True,
        "ipc_local_nats_port": 4299,
    })
    fake_mgr = MagicMock()
    fake_mgr.start.side_effect = RuntimeError("boom")
    import lightfall.ipc.local_server as ls
    monkeypatch.setattr(ls, "LocalNatsServer", MagicMock(return_value=fake_mgr))

    app = _app_without_init()
    svc = app._create_ipc_service(MagicMock())
    assert svc.nats_url == ""
    assert app._local_nats is None


def test_shutdown_stops_local_nats(monkeypatch):
    app = _app_without_init()
    app._state = None  # anything != ApplicationState.TERMINATED passes the guard
    app._set_state = MagicMock()
    app._services = MagicMock()
    app._services.get = MagicMock(return_value=None)
    fake_mgr = MagicMock()
    app._local_nats = fake_mgr

    app._shutdown()
    fake_mgr.stop.assert_called_once()
    assert app._local_nats is None


def test_enabled_bad_port_pref_does_not_crash(qapp, monkeypatch):
    _patch_prefs(monkeypatch, {
        "ipc_nats_url": "nats://site:4222",
        "ipc_topic_prefix": "als.7011",
        "ipc_use_local_nats": True,
        "ipc_local_nats_port": "abc",
    })
    fake_mgr = MagicMock()
    import lightfall.ipc.local_server as ls
    monkeypatch.setattr(ls, "LocalNatsServer", MagicMock(return_value=fake_mgr))

    app = _app_without_init()
    svc = app._create_ipc_service(MagicMock())
    # Bad port must not crash; falls back to 4222
    assert svc.nats_url == "nats://127.0.0.1:4222"
    fake_mgr.start.assert_called_once()
