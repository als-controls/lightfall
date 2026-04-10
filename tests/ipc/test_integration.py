"""Tests for BlueskyEngine <-> IPC integration wiring.

Verifies that NCSApplication._wire_engine_ipc and _wire_plan_commands
correctly bridge engine signals/commands with the IPCService, without
requiring a real NATS connection or Qt event loop.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lucid.ipc.service import IPCService


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
    svc._connected = False
    svc._connected_lock = threading.Lock()
    svc._loop = None
    svc._nc = None
    # Spy on publish / reply
    svc.publish = MagicMock()
    svc.reply = MagicMock()
    return svc


class _FakeEngine:
    """Minimal stand-in for BaseEngine with connectable signal mocks."""

    def __init__(self) -> None:
        self.sigOutput = _FakeSignal()
        self.sigFinish = _FakeSignal()
        self.sigAbort = _FakeSignal()
        self.sigException = _FakeSignal()
        self.sigStateChanged = _FakeSignal()

    def submit(self, procedure: Any, *, priority: int = 1, name: str = "", **kwargs: Any) -> str:
        return "fake-proc-id"

    def abort(self, reason: str = "") -> None:
        pass


class _FakeSignal:
    """Callable signal stub that records connected slots."""

    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in self._slots:
            slot(*args)


class _FakeServiceRegistry:
    """Minimal ServiceRegistry that returns injected objects by type."""

    def __init__(self, mapping: dict):
        self._mapping = mapping

    def get(self, service_type, default=None):
        return self._mapping.get(service_type, default)


class _FakePlanInfo:
    """Minimal PlanInfo stub."""

    def __init__(self, name: str, func):
        self.name = name
        self.func = func


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ipc():
    return _make_ipc()


@pytest.fixture()
def engine():
    return _FakeEngine()


@pytest.fixture()
def app(engine, ipc):
    """Build an NCSApplication without Qt/NATS and inject fakes."""
    from lucid.core.application import NCSApplication

    instance = NCSApplication.__new__(NCSApplication)
    instance._services = _FakeServiceRegistry({IPCService: ipc})
    return instance


# ---------------------------------------------------------------------------
# TestEngineIPCWiring
# ---------------------------------------------------------------------------


class TestEngineIPCWiring:
    """Tests for _wire_engine_ipc: engine signals -> IPC events."""

    def test_run_new_event_published_on_start_doc(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_engine_ipc()

        start_doc = {"uid": "abc-123", "plan_name": "count"}
        engine.sigOutput.emit("start", start_doc)

        ipc.publish.assert_called_once_with(
            "als.test.runs.new",
            {"run_id": "abc-123", "plan_name": "count"},
        )

    def test_non_start_docs_do_not_publish_run_new(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_engine_ipc()

        engine.sigOutput.emit("event", {"data": {}})
        ipc.publish.assert_not_called()

    def test_run_complete_success_on_finish(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_engine_ipc()

        # Simulate a run start so current_run is populated
        engine.sigOutput.emit("start", {"uid": "run-1", "plan_name": "scan"})
        ipc.publish.reset_mock()

        engine.sigFinish.emit()

        ipc.publish.assert_called_once_with(
            "als.test.runs.complete",
            {"run_id": "run-1", "exit_status": "success"},
        )

    def test_run_complete_abort_on_abort(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_engine_ipc()

        engine.sigOutput.emit("start", {"uid": "run-2", "plan_name": "scan"})
        ipc.publish.reset_mock()

        engine.sigAbort.emit()

        ipc.publish.assert_called_once_with(
            "als.test.runs.complete",
            {"run_id": "run-2", "exit_status": "abort"},
        )

    def test_run_complete_error_on_exception(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_engine_ipc()

        engine.sigOutput.emit("start", {"uid": "run-3", "plan_name": "scan"})
        ipc.publish.reset_mock()

        engine.sigException.emit(RuntimeError("boom"))

        ipc.publish.assert_called_once_with(
            "als.test.runs.complete",
            {"run_id": "run-3", "exit_status": "error"},
        )

    def test_state_changed_publishes_event(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_engine_ipc()

        engine.sigStateChanged.emit("running")

        ipc.publish.assert_called_once_with(
            "als.test.state.engine",
            {"state": "running"},
        )

    def test_events_registered_in_catalog(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_engine_ipc()

        assert "runs.new" in ipc._event_catalog
        assert "runs.complete" in ipc._event_catalog
        assert "state.engine" in ipc._event_catalog

    def test_finish_with_no_prior_start_uses_empty_run_id(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_engine_ipc()

        engine.sigFinish.emit()

        ipc.publish.assert_called_once_with(
            "als.test.runs.complete",
            {"run_id": "", "exit_status": "success"},
        )


# ---------------------------------------------------------------------------
# TestPlanCommandWiring
# ---------------------------------------------------------------------------


class TestPlanCommandWiring:
    """Tests for _wire_plan_commands: IPC commands -> engine actions."""

    def test_plan_run_missing_name_replies_error(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_plan_commands()

        # Find the handle_plan_run callback
        handler = ipc._subscriptions[ipc.topic("commands.plan.run")].callback
        handler("als.test.commands.plan.run", {}, "reply.inbox.1")

        ipc.reply.assert_called_once_with(
            "reply.inbox.1",
            {"error": True, "message": "plan_name is required"},
        )

    def test_plan_run_missing_name_no_reply_is_noop(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_plan_commands()

        handler = ipc._subscriptions[ipc.topic("commands.plan.run")].callback
        handler("als.test.commands.plan.run", {}, None)

        ipc.reply.assert_not_called()

    def test_plan_run_unknown_plan_replies_error(self, app, engine, ipc):
        with (
            patch("lucid.acquire.engine.get_engine", return_value=engine),
            patch("lucid.acquire.plans.registry.get_registry") as mock_reg,
        ):
            mock_reg.return_value.get_plan.return_value = None
            app._wire_plan_commands()

        handler = ipc._subscriptions[ipc.topic("commands.plan.run")].callback

        with patch("lucid.acquire.plans.registry.get_registry") as mock_reg:
            mock_reg.return_value.get_plan.return_value = None
            handler("als.test.commands.plan.run", {"plan_name": "nonexistent"}, "reply.inbox.2")

        ipc.reply.assert_called_once()
        payload = ipc.reply.call_args[0][1]
        assert payload["error"] is True
        assert "not found" in payload["message"]

    def test_plan_run_submits_and_replies(self, app, engine, ipc):
        def fake_plan(**kwargs):
            yield  # generator

        plan_info = _FakePlanInfo("count", fake_plan)

        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_plan_commands()

        handler = ipc._subscriptions[ipc.topic("commands.plan.run")].callback

        with patch("lucid.acquire.plans.registry.get_registry") as mock_reg:
            mock_reg.return_value.get_plan.return_value = plan_info
            handler(
                "als.test.commands.plan.run",
                {"plan_name": "count", "params": {"num": 5}},
                "reply.inbox.3",
            )

        ipc.reply.assert_called_once()
        payload = ipc.reply.call_args[0][1]
        assert payload["status"] == "submitted"
        assert payload["plan_name"] == "count"
        assert "procedure_id" in payload

    def test_plan_run_submit_failure_replies_error(self, app, engine, ipc):
        def bad_plan(**kwargs):
            raise TypeError("wrong arg")

        plan_info = _FakePlanInfo("bad", bad_plan)

        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_plan_commands()

        handler = ipc._subscriptions[ipc.topic("commands.plan.run")].callback

        with patch("lucid.acquire.plans.registry.get_registry") as mock_reg:
            mock_reg.return_value.get_plan.return_value = plan_info
            handler(
                "als.test.commands.plan.run",
                {"plan_name": "bad"},
                "reply.inbox.4",
            )

        ipc.reply.assert_called_once()
        payload = ipc.reply.call_args[0][1]
        assert payload["error"] is True
        assert "wrong arg" in payload["message"]

    def test_plan_abort_replies_success(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_plan_commands()

        handler = ipc._subscriptions[ipc.topic("commands.plan.abort")].callback
        handler("als.test.commands.plan.abort", {}, "reply.inbox.5")

        ipc.reply.assert_called_once_with(
            "reply.inbox.5",
            {"status": "abort_requested"},
        )

    def test_plan_abort_passes_reason(self, app, engine, ipc):
        engine.abort = MagicMock()

        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_plan_commands()

        handler = ipc._subscriptions[ipc.topic("commands.plan.abort")].callback
        handler("als.test.commands.plan.abort", {"reason": "user request"}, "reply.inbox.6")

        engine.abort.assert_called_once_with(reason="user request")

    def test_plan_abort_failure_replies_error(self, app, engine, ipc):
        engine.abort = MagicMock(side_effect=RuntimeError("not running"))

        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_plan_commands()

        handler = ipc._subscriptions[ipc.topic("commands.plan.abort")].callback
        handler("als.test.commands.plan.abort", {}, "reply.inbox.7")

        ipc.reply.assert_called_once()
        payload = ipc.reply.call_args[0][1]
        assert payload["error"] is True
        assert "not running" in payload["message"]

    def test_actions_registered_in_catalog(self, app, engine, ipc):
        with patch("lucid.acquire.engine.get_engine", return_value=engine):
            app._wire_plan_commands()

        assert "commands.plan.run" in ipc._action_catalog
        assert "commands.plan.abort" in ipc._action_catalog
