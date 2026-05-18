"""Tests for IPCService: topic builder, connection lifecycle, pub/sub."""

from __future__ import annotations

import asyncio
import os
import platform
from unittest.mock import MagicMock, patch

import pytest

from lucid.ipc.service import IPCService, _Subscription, ActionInfo, EventInfo


# ---------------------------------------------------------------------------
# TestTopicBuilder
# ---------------------------------------------------------------------------


class TestTopicBuilder:
    def _make(self, prefix: str) -> IPCService:
        """Create IPCService via __new__ + manual attribute injection to avoid
        Qt/NATS side effects during topic-builder unit tests."""
        svc = IPCService.__new__(IPCService)
        svc._topic_prefix = prefix
        return svc

    def test_topic_joins_prefix_and_suffix(self):
        svc = self._make("als.7011")
        assert svc.topic("commands.plan.run") == "als.7011.commands.plan.run"

    def test_topic_with_empty_prefix(self):
        svc = self._make("")
        assert svc.topic("commands.plan.run") == "commands.plan.run"


# ---------------------------------------------------------------------------
# TestConnectionLifecycle
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    def test_start_does_nothing_when_url_empty(self, qapp):
        svc = IPCService(nats_url="", topic_prefix="test")
        svc.start()
        assert svc._thread is None
        assert not svc.is_connected

    def test_is_connected_initially_false(self, qapp):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        assert not svc.is_connected

    def test_stop_without_start_is_safe(self, qapp):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        # Must not raise even though start() was never called
        svc.stop()


# ---------------------------------------------------------------------------
# TestSubscribePublish
# ---------------------------------------------------------------------------


class TestSubscribePublish:
    @pytest.fixture
    def svc(self, qapp):
        return IPCService(nats_url="nats://localhost:4222", topic_prefix="als.test")

    def test_subscribe_stores_subscription(self, svc):
        cb = MagicMock()
        svc.subscribe("als.test.events.foo", cb)
        assert "als.test.events.foo" in svc._subscriptions
        sub = svc._subscriptions["als.test.events.foo"]
        assert sub.callback is cb
        assert sub.main_thread is True  # default

    def test_subscribe_background_dispatch(self, svc):
        cb = MagicMock()
        svc.subscribe("als.test.events.bar", cb, main_thread=False)
        sub = svc._subscriptions["als.test.events.bar"]
        assert sub.main_thread is False

    def test_unsubscribe_removes_subscription(self, svc):
        cb = MagicMock()
        svc.subscribe("als.test.cmd.run", cb)
        assert "als.test.cmd.run" in svc._subscriptions
        svc.unsubscribe("als.test.cmd.run")
        assert "als.test.cmd.run" not in svc._subscriptions

    def test_unsubscribe_nonexistent_is_safe(self, svc):
        # Must not raise for an unknown subject
        svc.unsubscribe("nope.does.not.exist")

    def test_publish_drops_when_not_connected(self, svc):
        # svc has never been started — is_connected is False
        svc.publish("als.test.cmd.run", {"plan": "count"})  # must not raise

    def test_reply_drops_when_no_reply_subject_empty_string(self, svc):
        svc.reply("", {"status": "ok"})  # must not raise

    def test_reply_drops_when_no_reply_subject_none(self, svc):
        svc.reply(None, {"status": "ok"})  # must not raise


# ---------------------------------------------------------------------------
# TestDataClasses
# ---------------------------------------------------------------------------


class TestDataClasses:
    def test_action_info_fields(self):
        ai = ActionInfo(subject="als.cmd.run", description="Run a plan")
        assert ai.subject == "als.cmd.run"
        assert ai.description == "Run a plan"
        assert ai.schema is None

    def test_action_info_with_schema(self):
        schema = {"type": "object", "properties": {"plan": {"type": "string"}}}
        ai = ActionInfo(subject="als.cmd.run", description="Run a plan", schema=schema)
        assert ai.schema == schema

    def test_event_info_fields(self):
        ei = EventInfo(subject="als.events.started", description="Plan started")
        assert ei.subject == "als.events.started"
        assert ei.description == "Plan started"
        assert ei.schema is None

    def test_event_info_with_schema(self):
        schema = {"type": "object", "properties": {"status": {"type": "string"}}}
        ei = EventInfo(subject="als.events.started", description="Plan started", schema=schema)
        assert ei.schema == schema

    def test_subscription_fields(self):
        cb = MagicMock()
        sub = _Subscription(subject="als.test.foo", callback=cb, main_thread=True, nats_sub=None)
        assert sub.subject == "als.test.foo"
        assert sub.callback is cb
        assert sub.main_thread is True
        assert sub.nats_sub is None


# ---------------------------------------------------------------------------
# TestMakeHandler
# ---------------------------------------------------------------------------


class TestMakeHandler:
    def _make_svc(self):
        return IPCService(nats_url="nats://localhost:4222", topic_prefix="test")

    @patch("lucid.ipc.service.invoke_in_main_thread")
    def test_handler_dispatches_to_main_thread(self, mock_invoke, qapp):
        svc = self._make_svc()
        callback = MagicMock()
        sub = _Subscription(subject="test.foo", callback=callback, main_thread=True, nats_sub=None)
        handler = svc._make_handler(sub)

        msg = MagicMock()
        msg.subject = "test.foo"
        msg.data = b'{"key": "value"}'
        msg.reply = ""

        asyncio.run(handler(msg))
        mock_invoke.assert_called_once_with(callback, "test.foo", {"key": "value"}, "")

    def test_handler_calls_callback_directly_off_main_thread(self, qapp):
        svc = self._make_svc()
        callback = MagicMock()
        sub = _Subscription(subject="test.foo", callback=callback, main_thread=False, nats_sub=None)
        handler = svc._make_handler(sub)

        msg = MagicMock()
        msg.subject = "test.foo"
        msg.data = b'{"x": 42}'
        msg.reply = "test._INBOX.reply"

        asyncio.run(handler(msg))
        callback.assert_called_once_with("test.foo", {"x": 42}, "test._INBOX.reply")

    def test_handler_ignores_malformed_json(self, qapp):
        svc = self._make_svc()
        callback = MagicMock()
        sub = _Subscription(subject="test.foo", callback=callback, main_thread=False, nats_sub=None)
        handler = svc._make_handler(sub)

        msg = MagicMock()
        msg.subject = "test.foo"
        msg.data = b"not valid json {"
        msg.reply = ""

        asyncio.run(handler(msg))
        callback.assert_not_called()

    def test_handler_catches_callback_exception(self, qapp):
        svc = self._make_svc()
        callback = MagicMock(side_effect=RuntimeError("boom"))
        sub = _Subscription(subject="test.foo", callback=callback, main_thread=False, nats_sub=None)
        handler = svc._make_handler(sub)

        msg = MagicMock()
        msg.subject = "test.foo"
        msg.data = b'{"ok": true}'
        msg.reply = ""

        # Must not raise even though callback raises
        asyncio.run(handler(msg))
        callback.assert_called_once()


# ---------------------------------------------------------------------------
# TestAuthHandshake
# ---------------------------------------------------------------------------


from lucid.ipc.trust import TrustManager, TrustState


class TestAuthHandshake:
    def test_evaluate_trust_unknown_app(self):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        trust = TrustManager()
        svc.set_trust_manager(trust)
        assert svc.evaluate_trust("newapp") == TrustState.UNKNOWN

    def test_evaluate_trust_approved_app(self):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        trust = TrustManager()
        trust.approve("tsuchinoko")
        svc.set_trust_manager(trust)
        assert svc.evaluate_trust("tsuchinoko") == TrustState.APPROVED

    def test_evaluate_trust_denied_app(self):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        trust = TrustManager()
        trust.deny("badapp")
        svc.set_trust_manager(trust)
        assert svc.evaluate_trust("badapp") == TrustState.DENIED

    def test_evaluate_trust_no_manager_returns_denied(self):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        assert svc.evaluate_trust("anyapp") == TrustState.DENIED

    def test_build_auth_response_approved(self):
        """Approved response carries the cached Tiled API key under the
        legacy ``tiled_token`` field name (auth-v2; see method docstring)."""
        from datetime import UTC, datetime, timedelta

        from lucid.auth.service_key import MintedKey
        from lucid.auth.session import SessionManager

        SessionManager.reset()
        try:
            sm = SessionManager.get_instance()
            sm._service_keys["tiled"] = MintedKey(
                secret="tiled-apikey-xyz",
                first_eight="tiled-ap",
                expires_at=datetime.now(UTC) + timedelta(seconds=3600),
                scopes=("read:metadata",),
                note="test",
            )

            svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
            # session is still required as a presence flag, but its .token
            # is no longer read.
            mock_session = MagicMock()
            resp = svc.build_auth_response(
                approved=True,
                session=mock_session,
                tiled_url="https://tiled.example.com",
            )
            assert resp == {
                "status": "approved",
                "tiled_token": "tiled-apikey-xyz",
                "tiled_url": "https://tiled.example.com",
            }
        finally:
            SessionManager.reset()

    def test_build_auth_response_denied(self):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        resp = svc.build_auth_response(approved=False)
        assert resp == {"status": "denied"}

    def test_build_auth_response_denied_with_reason(self):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        resp = svc.build_auth_response(approved=False, reason="timeout")
        assert resp == {"status": "denied", "reason": "timeout"}


# ---------------------------------------------------------------------------
# TestRequest
# ---------------------------------------------------------------------------


class TestRequest:
    def test_request_returns_none_when_not_connected(self, qapp):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        # Never started, so not connected
        result = svc.request("test.ping", {})
        assert result is None

    def test_request_returns_none_when_no_loop(self, qapp):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        # Manually set connected but no loop
        with svc._connected_lock:
            svc._connected = True
        svc._loop = None
        result = svc.request("test.ping", {})
        assert result is None


# ---------------------------------------------------------------------------
# TestInstanceIdentity
# ---------------------------------------------------------------------------


class TestInstanceIdentity:
    @pytest.fixture
    def svc(self, qapp):
        return IPCService(nats_url="nats://localhost:4222", topic_prefix="als.test")

    def test_instance_id_contains_hostname(self, svc):
        assert platform.node() in svc.instance_id

    def test_instance_id_contains_pid(self, svc):
        assert str(os.getpid()) in svc.instance_id

    def test_display_name_defaults_to_none(self, svc):
        assert svc.display_name is None

    def test_display_name_settable(self, svc):
        svc.display_name = "CMS Hutch"
        assert svc.display_name == "CMS Hutch"
