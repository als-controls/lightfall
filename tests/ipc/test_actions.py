"""Tests for IPCService action/event catalog and meta discovery."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.ipc.service import IPCService, _ActionHandle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_svc(prefix: str = "als.test") -> IPCService:
    """Create an IPCService using __new__ to avoid Qt/NATS side effects."""
    import threading

    svc = IPCService.__new__(IPCService)
    svc._topic_prefix = prefix
    svc._subscriptions = {}
    svc._action_catalog = {}
    svc._event_catalog = {}
    svc._trusted_actions = {}
    svc._session_channels = {}
    svc._connected = False
    svc._connected_lock = threading.Lock()
    svc._loop = None
    svc._nc = None
    svc._instance_id = "testhost-0"
    svc._display_name = None
    return svc


# ---------------------------------------------------------------------------
# TestActionRegistration
# ---------------------------------------------------------------------------


class TestActionRegistration:
    def test_register_action_adds_to_catalog(self):
        svc = _make_svc()
        schema = {"type": "object"}
        svc.register_action("cmd.run", MagicMock(), description="Run a plan", schema=schema)

        assert "cmd.run" in svc._action_catalog
        info = svc._action_catalog["cmd.run"]
        assert info.description == "Run a plan"
        assert info.schema == schema

    def test_register_action_creates_subscription(self):
        svc = _make_svc()
        svc.register_action("cmd.run", MagicMock())

        full_subject = svc.topic("cmd.run")  # "als.test.cmd.run"
        assert full_subject in svc._subscriptions

    def test_unregister_action_removes_catalog_and_subscription(self):
        svc = _make_svc()
        handle = svc.register_action("cmd.stop", MagicMock())

        full_subject = svc.topic("cmd.stop")
        assert "cmd.stop" in svc._action_catalog
        assert full_subject in svc._subscriptions

        handle.unregister()

        assert "cmd.stop" not in svc._action_catalog
        assert full_subject not in svc._subscriptions


# ---------------------------------------------------------------------------
# TestEventRegistration
# ---------------------------------------------------------------------------


class TestEventRegistration:
    def test_register_event_adds_to_catalog(self):
        svc = _make_svc()
        schema = {"type": "object", "properties": {"status": {"type": "string"}}}
        svc.register_event("events.scan.started", description="Scan started", schema=schema)

        assert "events.scan.started" in svc._event_catalog
        info = svc._event_catalog["events.scan.started"]
        assert info.description == "Scan started"
        assert info.schema == schema

    def test_register_event_does_not_create_subscription(self):
        svc = _make_svc()
        svc.register_event("events.scan.started")

        full_subject = svc.topic("events.scan.started")
        assert full_subject not in svc._subscriptions


# ---------------------------------------------------------------------------
# TestMetaDiscovery
# ---------------------------------------------------------------------------


class TestMetaDiscovery:
    def test_list_actions(self):
        svc = _make_svc()
        svc.register_action("cmd.run", MagicMock(), description="Run a plan")
        svc.register_action("cmd.stop", MagicMock(), description="Stop a plan")

        actions = svc.list_actions()
        assert len(actions) == 2
        subjects = {a["subject"] for a in actions}
        assert subjects == {"cmd.run", "cmd.stop"}
        for entry in actions:
            assert "subject" in entry
            assert "description" in entry
            assert "schema" in entry

    def test_list_events(self):
        svc = _make_svc()
        svc.register_event("events.scan.started", description="Scan started")
        svc.register_event("events.scan.stopped", description="Scan stopped")

        events = svc.list_events()
        assert len(events) == 2
        subjects = {e["subject"] for e in events}
        assert subjects == {"events.scan.started", "events.scan.stopped"}
        for entry in events:
            assert "subject" in entry
            assert "description" in entry
            assert "schema" in entry

    def test_handle_meta_actions_sends_reply(self):
        svc = _make_svc()
        svc.register_action("cmd.run", MagicMock(), description="Run")
        svc.reply = MagicMock()

        svc._handle_meta_actions("als.test.meta.actions", {}, "reply.inbox.123")

        svc.reply.assert_called_once()
        call_args = svc.reply.call_args
        assert call_args[0][0] == "reply.inbox.123"
        payload = call_args[0][1]
        assert "actions" in payload
        assert len(payload["actions"]) == 1

    def test_handle_meta_actions_no_reply_is_noop(self):
        svc = _make_svc()
        svc.reply = MagicMock()

        svc._handle_meta_actions("als.test.meta.actions", {}, None)
        svc._handle_meta_actions("als.test.meta.actions", {}, "")

        svc.reply.assert_not_called()

    def test_handle_meta_events_sends_reply(self):
        svc = _make_svc()
        svc.register_event("events.scan.started")
        svc.reply = MagicMock()

        svc._handle_meta_events("als.test.meta.events", {}, "reply.inbox.456")

        svc.reply.assert_called_once()
        payload = svc.reply.call_args[0][1]
        assert "events" in payload
        assert len(payload["events"]) == 1

    def test_register_meta_endpoints_adds_catalog_entries(self):
        svc = _make_svc()
        svc.register_meta_endpoints()

        assert "meta.actions" in svc._action_catalog
        assert "meta.events" in svc._action_catalog


# ---------------------------------------------------------------------------
# TestMetaResponseIdentity
# ---------------------------------------------------------------------------


class TestMetaResponseIdentity:
    def test_meta_actions_includes_identity(self):
        svc = _make_svc(prefix="als.test")
        svc._instance_id = "testhost-999"
        svc._display_name = "Test Hutch"
        svc.register_meta_endpoints()
        svc.reply = MagicMock()

        svc._handle_meta_actions("als.test.meta.actions", {}, "reply.inbox")

        svc.reply.assert_called_once()
        response = svc.reply.call_args[0][1]
        assert response["instance_id"] == "testhost-999"
        assert response["display_name"] == "Test Hutch"
        assert response["prefix"] == "als.test"
        assert "actions" in response

    def test_meta_events_includes_identity(self):
        svc = _make_svc(prefix="als.test")
        svc._instance_id = "testhost-999"
        svc._display_name = None
        svc.register_meta_endpoints()
        svc.reply = MagicMock()

        svc._handle_meta_events("als.test.meta.events", {}, "reply.inbox")

        svc.reply.assert_called_once()
        response = svc.reply.call_args[0][1]
        assert response["instance_id"] == "testhost-999"
        assert response["display_name"] is None
        assert response["prefix"] == "als.test"
        assert "events" in response


# ---------------------------------------------------------------------------
# TestDiscoverEndpoint
# ---------------------------------------------------------------------------


class TestDiscoverEndpoint:
    def test_discover_handler_registered(self):
        svc = _make_svc(prefix="als.test")
        svc.subscribe = MagicMock()
        svc.register_meta_endpoints()
        subjects = [call[0][0] for call in svc.subscribe.call_args_list]
        assert "_lightfall.discover" in subjects

    def test_discover_response_matches_meta_actions(self):
        svc = _make_svc(prefix="als.test")
        svc._instance_id = "testhost-999"
        svc._display_name = "Test Hutch"
        svc.register_meta_endpoints()
        svc.register_action("commands.echo", lambda s, d, r: None, description="Echo back", schema={"msg": "str"})
        svc.reply = MagicMock()
        svc._handle_discover("_lightfall.discover", {}, "reply.inbox")
        svc.reply.assert_called_once()
        response = svc.reply.call_args[0][1]
        assert response["instance_id"] == "testhost-999"
        assert response["display_name"] == "Test Hutch"
        assert response["prefix"] == "als.test"
        assert isinstance(response["actions"], list)
