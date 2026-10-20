"""Tests for Lightfall's wiring onto IPCService's capability-channel auth reply.

Uses the same fake-IPCService construction pattern as tests/ipc/test_integration.py:
IPCService.__new__ + manual attribute injection, capturing outbound replies.

The pure-service capability-channel tests (mint/route/teardown, with no
Lightfall auth wiring) moved to lightfall-utils along with the implementation.
"""

from __future__ import annotations

import threading

from lightfall.ipc.service import IPCService
from lightfall_utils.ipc.capability import new_capability


def _make_ipc(prefix: str = "als.test") -> tuple[IPCService, list[tuple[str, dict]]]:
    """Build a disconnected IPCService whose publish() captures messages."""
    ipc = IPCService.__new__(IPCService)
    ipc._topic_prefix = prefix
    ipc._subscriptions = {}
    ipc._action_catalog = {}
    ipc._event_catalog = {}
    ipc._trusted_actions = {}
    ipc._session_channels = {}
    ipc._trust = None
    ipc._instance_id = "test-1"
    ipc._display_name = None
    ipc._loop = None
    ipc._nc = None
    ipc._connected = False
    ipc._connected_lock = threading.Lock()
    ipc._capability = new_capability()

    sent: list[tuple[str, dict]] = []
    ipc.publish = lambda subject, data: sent.append((subject, data))  # type: ignore[method-assign]
    return ipc, sent


class TestAuthResponse:
    def test_approved_reply_carries_session_token_and_version(self, monkeypatch):
        """Lightfall's own approved-reply builder (Tiled fields via `extra`)
        still ends up minting a session capability channel through the
        shared service."""
        from lightfall.core.application import LFApplication

        ipc, _ = _make_ipc()

        class _FakeSM:
            def get_api_key(self, service):
                return "key123"

        import lightfall.auth.session as session_mod

        monkeypatch.setattr(
            session_mod.SessionManager, "get_instance", staticmethod(lambda: _FakeSM())
        )

        class _Sess:
            class user:
                attributes = {"sub": "user-1"}

        app = LFApplication.__new__(LFApplication)
        app._get_current_session = lambda: _Sess()
        app._get_tiled_url = lambda: "http://t"

        resp = app._approved_auth_reply(ipc, "pystxm")
        assert resp["status"] == "approved"
        assert resp["contract_version"] == 1
        assert resp["tiled_token"] == "key123"
        assert resp["tiled_url"] == "http://t"
        assert resp["session_id"] == "user-1"
        token = resp["session_token"]
        assert f"{ipc.priv_root}.session.{token}.>" in ipc._subscriptions
