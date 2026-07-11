"""Tests for LFApplication IPC wiring (logbook, agent).

Engine/plan wiring (run-lifecycle events, engine.status, queue.get, plan
verbs) moved to RemoteControlService — see tests/remote/test_service_plan.py.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from lightfall.ipc.service import IPCService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ipc(prefix: str = "als.test") -> IPCService:
    """Lightweight IPCService that skips Qt/NATS init."""
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
    # Spy on publish / reply
    svc.publish = MagicMock()
    svc.reply = MagicMock()
    return svc


class _FakeServiceRegistry:
    """Minimal ServiceRegistry that returns injected objects by type."""

    def __init__(self, mapping: dict):
        self._mapping = mapping

    def get(self, service_type, default=None):
        return self._mapping.get(service_type, default)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ipc():
    return _make_ipc()


@pytest.fixture()
def app(ipc):
    """Build an LFApplication without Qt/NATS and inject fakes."""
    from lightfall.core.application import LFApplication

    instance = LFApplication.__new__(LFApplication)
    instance._services = _FakeServiceRegistry({IPCService: ipc})
    return instance


# ---------------------------------------------------------------------------
# TestLogbookIPCIntegration
# ---------------------------------------------------------------------------


class _FakeUser:
    """Minimal User stub with a username."""

    def __init__(self, username: str | None = None):
        self.username = username


class _FakeSessionManager:
    """Minimal SessionManager stub."""

    def __init__(self, user: _FakeUser | None = None):
        self._user = user or _FakeUser()

    @property
    def current_user(self):
        return self._user

    @classmethod
    def get_instance(cls):
        # Overridden via patch in each test
        raise NotImplementedError


class TestLogbookIPCIntegration:
    """Tests for _wire_logbook_ipc: IPC commands -> LogbookClient."""

    def test_logbook_add_creates_entry_and_fragment(self, app, ipc):
        mock_client = MagicMock()
        mock_client.get_or_create_logbook.return_value = "logbook-1"
        mock_client.create_entry.return_value = "entry-1"

        fake_sm = _FakeSessionManager(_FakeUser("testuser"))

        with (
            patch("lightfall.logbook.client.LogbookClient.get_instance", return_value=mock_client),
            patch("lightfall.auth.session.SessionManager.get_instance", return_value=fake_sm),
        ):
            app._wire_logbook_ipc()

            handler = ipc._trusted_actions["commands.logbook.add"].callback
            handler(
                "als.test.commands.logbook.add",
                {"title": "Shift 1", "content": "Started alignment", "tags": ["shift"]},
                "reply.inbox.10",
            )

        mock_client.get_or_create_logbook.assert_called_once_with("testuser")
        mock_client.create_entry.assert_called_once_with(
            "logbook-1", title="Shift 1", tags=["shift"]
        )
        mock_client.add_fragment.assert_called_once_with("entry-1", content="Started alignment")

        ipc.reply.assert_called_once_with(
            "reply.inbox.10",
            {"status": "created", "entry_id": "entry-1"},
        )

    def test_logbook_add_no_active_logbook_returns_error(self, app, ipc):
        """No authenticated user -> error response."""
        fake_sm = _FakeSessionManager(_FakeUser(username=None))

        with (
            patch("lightfall.logbook.client.LogbookClient.get_instance", return_value=MagicMock()),
            patch("lightfall.auth.session.SessionManager.get_instance", return_value=fake_sm),
        ):
            app._wire_logbook_ipc()

            handler = ipc._trusted_actions["commands.logbook.add"].callback
            handler(
                "als.test.commands.logbook.add",
                {"title": "Test"},
                "reply.inbox.11",
            )

        ipc.reply.assert_called_once()
        payload = ipc.reply.call_args[0][1]
        assert payload["error"] is True
        assert "No active logbook" in payload["message"]

    def test_logbook_add_missing_content_skips_fragment(self, app, ipc):
        """Only title, no content -> create_entry but NOT add_fragment."""
        mock_client = MagicMock()
        mock_client.get_or_create_logbook.return_value = "logbook-2"
        mock_client.create_entry.return_value = "entry-2"

        fake_sm = _FakeSessionManager(_FakeUser("testuser"))

        with (
            patch("lightfall.logbook.client.LogbookClient.get_instance", return_value=mock_client),
            patch("lightfall.auth.session.SessionManager.get_instance", return_value=fake_sm),
        ):
            app._wire_logbook_ipc()

            handler = ipc._trusted_actions["commands.logbook.add"].callback
            handler(
                "als.test.commands.logbook.add",
                {"title": "Empty entry"},
                "reply.inbox.12",
            )

        mock_client.create_entry.assert_called_once_with(
            "logbook-2", title="Empty entry", tags=[]
        )
        mock_client.add_fragment.assert_not_called()

        ipc.reply.assert_called_once_with(
            "reply.inbox.12",
            {"status": "created", "entry_id": "entry-2"},
        )


# ---------------------------------------------------------------------------
# TestAgentIPCIntegration
# ---------------------------------------------------------------------------


class TestAgentIPCIntegration:
    """Tests for _wire_agent_ipc: IPC commands -> QtClaudeAgent."""

    def test_agent_message_calls_query_sync(self, app, ipc):
        mock_agent = MagicMock()
        mock_widget = MagicMock()
        mock_widget.agent = mock_agent

        mock_main_window = MagicMock()
        mock_main_window.findChild.return_value = mock_widget
        app._main_window = mock_main_window

        app._wire_agent_ipc()

        handler = ipc._trusted_actions["commands.agent.message"].callback
        handler(
            "als.test.commands.agent.message",
            {"message": "What is the beam energy?"},
            "reply.inbox.20",
        )

        mock_agent.query_sync.assert_called_once_with("What is the beam energy?")
        ipc.reply.assert_called_once_with(
            "reply.inbox.20",
            {"status": "sent"},
        )

    def test_agent_message_empty_returns_error(self, app, ipc):
        app._wire_agent_ipc()

        handler = ipc._trusted_actions["commands.agent.message"].callback
        handler(
            "als.test.commands.agent.message",
            {"message": ""},
            "reply.inbox.21",
        )

        ipc.reply.assert_called_once()
        payload = ipc.reply.call_args[0][1]
        assert payload["error"] is True
        assert "message is required" in payload["message"]


# ---------------------------------------------------------------------------
# TestLogbookIPCRefresh
# ---------------------------------------------------------------------------


class TestLogbookIPCRefresh:
    """Verify LogbookClient fires entry-created callback."""

    def _make_client(self):
        import sqlite3

        from lightfall.logbook.client import LogbookClient

        client = LogbookClient.__new__(LogbookClient)
        client._db = None
        client._sync_timer = None
        client._on_pull_callback = None
        client._on_sync_error_callback = None
        client._on_sync_restored_callback = None
        client._on_entry_created_callback = None
        client._sync_failed = False
        client._offline_only = True
        client._server_url = None

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS logbook (id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS entry (id TEXT PRIMARY KEY, logbook_id TEXT, title TEXT, tags TEXT, created_at TEXT, updated_at TEXT, sync_status TEXT);
            CREATE TABLE IF NOT EXISTS fragment (id TEXT PRIMARY KEY, entry_id TEXT, kind TEXT, subtype TEXT, content TEXT, data TEXT, position INTEGER, created_at TEXT, updated_at TEXT, sync_status TEXT);
        """)
        client._db = db
        client._initialized = True
        return client, db

    def test_create_entry_fires_callback(self, qapp):
        client, db = self._make_client()

        captured = []
        client.set_on_entry_created_callback(lambda eid, lid: captured.append((eid, lid)))

        logbook_id = "test-logbook"
        db.execute(
            "INSERT INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)",
            (logbook_id, "testuser", "2026-01-01"),
        )
        db.commit()

        entry_id = client.create_entry(logbook_id, title="IPC Test")

        # Callback is deferred via QTimer.singleShot(0, ...) — process events
        qapp.processEvents()

        assert len(captured) == 1
        assert captured[0] == (entry_id, logbook_id)

    def test_no_callback_no_error(self):
        """create_entry doesn't crash when no callback is registered."""
        client, db = self._make_client()

        db.execute(
            "INSERT INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)",
            ("lb", "testuser", "2026-01-01"),
        )
        db.commit()

        entry_id = client.create_entry("lb", title="No callback")
        assert entry_id
