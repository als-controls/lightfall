"""Tests for IPCService: topic builder, connection lifecycle, pub/sub."""

from __future__ import annotations

from unittest.mock import MagicMock

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
        ai = ActionInfo(name="run", subject="als.cmd.run", description="Run a plan")
        assert ai.name == "run"
        assert ai.subject == "als.cmd.run"
        assert ai.description == "Run a plan"

    def test_event_info_fields(self):
        ei = EventInfo(name="started", subject="als.events.started", description="Plan started")
        assert ei.name == "started"
        assert ei.subject == "als.events.started"
        assert ei.description == "Plan started"

    def test_subscription_fields(self):
        cb = MagicMock()
        sub = _Subscription(callback=cb, main_thread=True, nats_sub=None)
        assert sub.callback is cb
        assert sub.main_thread is True
        assert sub.nats_sub is None
