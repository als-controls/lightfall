"""Logout must clear trust and tear down capability channels (spec §3.5)."""

from __future__ import annotations

import threading

from lightfall.auth.session import AuthState
from lightfall.core.application import LFApplication
from lightfall.ipc.service import IPCService
from lightfall.ipc.trust import TrustManager


class _FakeRegistry:
    def __init__(self, services):
        self._services = services

    def get(self, service_type, default=None):
        return self._services.get(service_type, default)


def _make_ipc() -> IPCService:
    ipc = IPCService.__new__(IPCService)
    ipc._topic_prefix = "als.test"
    ipc._subscriptions = {}
    ipc._action_catalog = {}
    ipc._event_catalog = {}
    ipc._trusted_actions = {}
    ipc._session_channels = {}
    ipc._trust = None
    ipc._instance_id = "t"
    ipc._display_name = None
    ipc._loop = None
    ipc._nc = None
    ipc._connected = False
    ipc._connected_lock = threading.Lock()
    ipc.publish = lambda subject, data: None  # type: ignore[method-assign]
    return ipc


def test_logout_clears_trust_and_channels(qapp, monkeypatch):
    ipc = _make_ipc()
    trust = TrustManager()
    trust.approve("pystxm")
    ipc.mint_session_channel("pystxm")

    app = LFApplication.__new__(LFApplication)
    app._services = _FakeRegistry({IPCService: ipc, TrustManager: trust})

    class _FakeSM:
        class _Sig:
            def __init__(self):
                self._slots = []

            def connect(self, slot):
                self._slots.append(slot)

            def emit(self, *args):
                for s in self._slots:
                    s(*args)

        def __init__(self):
            self.state_changed = self._Sig()

    sm = _FakeSM()
    import lightfall.auth.session as session_mod

    monkeypatch.setattr(session_mod.SessionManager, "get_instance", staticmethod(lambda: sm))

    app._wire_session_trust()
    sm.state_changed.emit(AuthState.UNAUTHENTICATED, AuthState.AUTHENTICATED)

    assert not trust.is_trusted("pystxm")
    assert ipc.session_channel_count == 0


def test_login_transition_does_not_clear(qapp, monkeypatch):
    ipc = _make_ipc()
    trust = TrustManager()
    trust.approve("pystxm")
    ipc.mint_session_channel("pystxm")

    app = LFApplication.__new__(LFApplication)
    app._services = _FakeRegistry({IPCService: ipc, TrustManager: trust})

    class _FakeSM:
        class _Sig:
            def __init__(self):
                self._slots = []

            def connect(self, slot):
                self._slots.append(slot)

            def emit(self, *args):
                for s in self._slots:
                    s(*args)

        def __init__(self):
            self.state_changed = self._Sig()

    sm = _FakeSM()
    import lightfall.auth.session as session_mod

    monkeypatch.setattr(session_mod.SessionManager, "get_instance", staticmethod(lambda: sm))

    app._wire_session_trust()
    sm.state_changed.emit(AuthState.AUTHENTICATED, AuthState.UNAUTHENTICATED)

    assert trust.is_trusted("pystxm")
    assert ipc.session_channel_count == 1
