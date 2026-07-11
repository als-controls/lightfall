"""Unit tests for IPCService capability-channel trust plumbing.

Uses the same fake-IPCService construction pattern as tests/ipc/test_integration.py:
IPCService.__new__ + manual attribute injection, capturing outbound replies.
"""

from __future__ import annotations

import threading

import pytest

from lightfall.ipc.service import IPCService


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

    sent: list[tuple[str, dict]] = []
    ipc.publish = lambda subject, data: sent.append((subject, data))  # type: ignore[method-assign]
    return ipc, sent


def _call_subscription(ipc: IPCService, subject: str, data: dict, reply: str) -> None:
    """Invoke the callback registered for *subject* directly (as _make_handler would)."""
    ipc._subscriptions[subject].callback(subject, data, reply)


class TestMint:
    def test_mint_returns_unguessable_token_and_subscribes_wildcard(self):
        ipc, _ = _make_ipc()
        token = ipc.mint_session_channel("pystxm")
        assert len(token) >= 22  # token_urlsafe(32) -> 43 chars; 128-bit floor
        assert f"als.test.session.{token}.>" in ipc._subscriptions

    def test_mint_twice_gives_distinct_tokens(self):
        ipc, _ = _make_ipc()
        assert ipc.mint_session_channel("a") != ipc.mint_session_channel("a")
        assert ipc.session_channel_count == 2


class TestRouting:
    def test_trusted_action_reachable_via_channel_with_identity(self):
        ipc, sent = _make_ipc()
        calls: list[tuple[str, dict]] = []
        ipc.register_action(
            "commands.thing.do",
            lambda s, d, r: calls.append((s, d)),
            trusted=True,
            main_thread=False,
        )
        token = ipc.mint_session_channel("pystxm")
        wildcard = f"als.test.session.{token}.>"
        assert wildcard in ipc._subscriptions
        # Deliver a request the way _make_handler would: the wildcard
        # subscription's callback invoked with the REAL message subject.
        ipc._subscriptions[wildcard].callback(
            f"als.test.session.{token}.commands.thing.do", {"x": 1}, "_INBOX.1"
        )
        assert calls == [
            (
                "commands.thing.do",
                {"x": 1, "_identity": {"app_name": "pystxm", "session_token": token}},
            )
        ]

    def test_router_dispatches_by_real_subject(self):
        ipc, sent = _make_ipc()
        seen: list[tuple[str, dict]] = []
        ipc.register_action(
            "commands.thing.do",
            lambda s, d, r: seen.append((s, d)),
            trusted=True,
            main_thread=False,
        )
        token = ipc.mint_session_channel("pystxm")
        router = ipc._subscriptions[f"als.test.session.{token}.>"].callback
        router(f"als.test.session.{token}.commands.thing.do", {"x": 1}, "_INBOX.1")
        assert len(seen) == 1
        subject, data = seen[0]
        assert subject == "commands.thing.do"
        assert data["x"] == 1
        assert data["_identity"] == {"app_name": "pystxm", "session_token": token}

    def test_client_supplied_identity_is_stripped(self):
        ipc, _ = _make_ipc()
        seen: list[dict] = []
        ipc.register_action("commands.t.d", lambda s, d, r: seen.append(d), trusted=True, main_thread=False)
        token = ipc.mint_session_channel("appA")
        router = ipc._subscriptions[f"als.test.session.{token}.>"].callback
        router(f"als.test.session.{token}.commands.t.d", {"_identity": {"app_name": "fake"}}, "_INBOX.1")
        assert seen[0]["_identity"]["app_name"] == "appA"

    def test_unknown_suffix_on_channel_gets_unknown_error(self):
        ipc, sent = _make_ipc()
        token = ipc.mint_session_channel("appA")
        router = ipc._subscriptions[f"als.test.session.{token}.>"].callback
        router(f"als.test.session.{token}.commands.nope", {}, "_INBOX.9")
        assert sent[-1][0] == "_INBOX.9"
        assert sent[-1][1]["status"] == "error"
        assert sent[-1][1]["code"] == "unknown"

    def test_untrusted_action_not_reachable_via_channel(self):
        ipc, sent = _make_ipc()
        ipc.register_action("meta.actions", lambda s, d, r: None)  # untrusted
        token = ipc.mint_session_channel("appA")
        router = ipc._subscriptions[f"als.test.session.{token}.>"].callback
        router(f"als.test.session.{token}.meta.actions", {}, "_INBOX.2")
        assert sent[-1][1]["code"] == "unknown"

    def test_version_mismatch_rejected(self):
        ipc, sent = _make_ipc()
        ipc.register_action("commands.t.d", lambda s, d, r: None, trusted=True, main_thread=False)
        token = ipc.mint_session_channel("appA")
        router = ipc._subscriptions[f"als.test.session.{token}.>"].callback
        router(f"als.test.session.{token}.commands.t.d", {"contract_version": 2}, "_INBOX.3")
        assert sent[-1][1]["code"] == "version_mismatch"


class TestBareSubjectRejection:
    def test_bare_commands_subject_replies_denied(self):
        ipc, sent = _make_ipc()
        ipc.register_action("commands.plan.run", lambda s, d, r: None, trusted=True)
        _call_subscription(ipc, "als.test.commands.plan.run", {"plan_name": "x"}, "_INBOX.4")
        assert sent[-1][0] == "_INBOX.4"
        assert sent[-1][1]["status"] == "error"
        assert sent[-1][1]["code"] == "denied"

    def test_trusted_action_still_in_catalog(self):
        ipc, _ = _make_ipc()
        ipc.register_action("commands.plan.run", lambda s, d, r: None, trusted=True)
        assert any(a["subject"] == "commands.plan.run" for a in ipc.list_actions())

    def test_unregister_trusted_action_removes_both(self):
        ipc, _ = _make_ipc()
        handle = ipc.register_action("commands.plan.run", lambda s, d, r: None, trusted=True)
        handle.unregister()
        assert "commands.plan.run" not in ipc._trusted_actions
        assert "als.test.commands.plan.run" not in ipc._subscriptions


class TestTeardown:
    def test_teardown_all(self):
        ipc, _ = _make_ipc()
        t1 = ipc.mint_session_channel("a")
        t2 = ipc.mint_session_channel("b")
        ipc.teardown_session_channels()
        assert ipc.session_channel_count == 0
        assert f"als.test.session.{t1}.>" not in ipc._subscriptions
        assert f"als.test.session.{t2}.>" not in ipc._subscriptions

    def test_teardown_single_app(self):
        ipc, _ = _make_ipc()
        ta = ipc.mint_session_channel("a")
        tb = ipc.mint_session_channel("b")
        ipc.teardown_session_channels("a")
        assert ipc.session_channel_count == 1
        assert f"als.test.session.{ta}.>" not in ipc._subscriptions
        assert f"als.test.session.{tb}.>" in ipc._subscriptions

    def test_dead_token_routes_nowhere(self):
        ipc, sent = _make_ipc()
        seen = []
        ipc.register_action("commands.t.d", lambda s, d, r: seen.append(d), trusted=True, main_thread=False)
        token = ipc.mint_session_channel("a")
        router = ipc._subscriptions[f"als.test.session.{token}.>"].callback
        ipc.teardown_session_channels()
        router(f"als.test.session.{token}.commands.t.d", {}, "_INBOX.5")
        assert seen == []
        assert sent[-1][1]["code"] == "denied"


class TestAuthResponse:
    def test_approved_response_carries_session_token_and_version(self, monkeypatch):
        ipc, _ = _make_ipc()

        class _FakeSM:
            def get_api_key(self, service):
                return "key123"

        import lightfall.auth.session as session_mod

        monkeypatch.setattr(session_mod.SessionManager, "get_instance", staticmethod(lambda: _FakeSM()))

        class _Sess:
            class user:
                attributes = {"sub": "user-1"}

        resp = ipc.build_auth_response(
            approved=True, session=_Sess(), tiled_url="http://t", app_name="pystxm"
        )
        assert resp["status"] == "approved"
        assert resp["contract_version"] == 1
        token = resp["session_token"]
        assert f"als.test.session.{token}.>" in ipc._subscriptions

    def test_denied_response_has_no_token(self):
        ipc, _ = _make_ipc()
        resp = ipc.build_auth_response(approved=False, reason="denied")
        assert resp == {"status": "denied", "reason": "denied", "contract_version": 1}
