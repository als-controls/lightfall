"""RemoteControlService: run-lifecycle events, engine.status, queue.get.

Plan verbs (plan.list/run/abort) are covered further down (Task 5 extends
this file).
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from lightfall.ipc.service import IPCService
from lightfall.remote.service import RemoteControlService


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for s in self._slots:
            s(*args)


class _FakeEngine:
    def __init__(self):
        self.sigOutput = _FakeSignal()
        self.sigFinish = _FakeSignal()
        self.sigAbort = _FakeSignal()
        self.sigException = _FakeSignal()
        self.sigStateChanged = _FakeSignal()
        self.is_idle = True
        self._queue = []
        self._current = None
        self.submitted = []

    def submit(self, procedure, *, name="", **kwargs):
        self.submitted.append((procedure, name))
        return "item-1"

    def get_queue_items(self):
        return list(self._queue)

    def get_current_procedure(self):
        return self._current

    def abort(self, reason=""):
        return True

    @property
    def state_name(self):
        return "idle" if self.is_idle else "running"


def _make_ipc():
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
    sent = []
    ipc.publish = lambda subject, data: sent.append((subject, data))  # type: ignore[method-assign]
    return ipc, sent


@pytest.fixture
def svc(qapp):
    ipc, sent = _make_ipc()
    engine = _FakeEngine()
    service = RemoteControlService(ipc, engine=engine, catalog=None)
    service.start()
    yield SimpleNamespace(ipc=ipc, sent=sent, engine=engine, service=service)
    service.stop()


def _invoke(svc, suffix, data, reply="_INBOX.r"):
    """Call a trusted action handler directly and wait for its (possibly
    executor-dispatched) reply to land."""
    svc.ipc._trusted_actions[suffix].callback(suffix, data, reply)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        replies = [d for s, d in svc.sent if s == reply]
        if replies:
            return replies[-1]
        time.sleep(0.01)
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.processEvents()
    raise AssertionError(f"No reply for {suffix}")


class TestEvents:
    def test_start_doc_publishes_runs_new_with_item_and_uid(self, svc):
        svc.engine._current = SimpleNamespace(id="item-7", name="scan")
        svc.engine.sigOutput.emit("start", {"uid": "uid-1", "plan_name": "scan"})
        subjects = dict(svc.sent)
        assert subjects["als.test.runs.new"] == {
            "item_id": "item-7",
            "run_uid": "uid-1",
            "plan_name": "scan",
        }

    def test_finish_publishes_runs_complete_run_uid(self, svc):
        svc.engine._current = SimpleNamespace(id="item-7", name="scan")
        svc.engine.sigOutput.emit("start", {"uid": "uid-1", "plan_name": "scan"})
        svc.engine.sigFinish.emit()
        assert ("als.test.runs.complete", {"run_uid": "uid-1", "exit_status": "success"}) in svc.sent

    def test_abort_and_exception_exit_statuses(self, svc):
        svc.engine._current = SimpleNamespace(id="i", name="p")
        svc.engine.sigOutput.emit("start", {"uid": "u1", "plan_name": "p"})
        svc.engine.sigAbort.emit()
        assert ("als.test.runs.complete", {"run_uid": "u1", "exit_status": "abort"}) in svc.sent
        svc.engine.sigOutput.emit("start", {"uid": "u2", "plan_name": "p"})
        svc.engine.sigException.emit(RuntimeError("x"))
        assert ("als.test.runs.complete", {"run_uid": "u2", "exit_status": "error"}) in svc.sent

    def test_state_change_published(self, svc):
        svc.engine.sigStateChanged.emit("running")
        assert ("als.test.state.engine", {"state": "running"}) in svc.sent

    def test_events_registered_in_catalog(self, svc):
        events = {e["subject"] for e in svc.ipc.list_events()}
        assert {"runs.new", "runs.complete", "state.engine"} <= events


class TestEngineStatus:
    def test_idle_status(self, svc):
        reply = _invoke(svc, "commands.engine.status", {})
        assert reply["state"] == "idle"
        assert reply["contract_version"] == 1

    def test_running_status_includes_current_run(self, svc):
        svc.engine.is_idle = False
        svc.engine._current = SimpleNamespace(id="item-7", name="scan")
        svc.engine.sigOutput.emit("start", {"uid": "uid-1", "plan_name": "scan"})
        reply = _invoke(svc, "commands.engine.status", {})
        assert reply == {
            "state": "running",
            "item_id": "item-7",
            "run_uid": "uid-1",
            "plan_name": "scan",
            "contract_version": 1,
        }


class TestQueueGet:
    def test_empty_queue(self, svc):
        reply = _invoke(svc, "commands.queue.get", {})
        assert reply == {"items": [], "contract_version": 1}

    def test_queued_and_running_items(self, svc):
        svc.engine.is_idle = False
        svc.engine._current = SimpleNamespace(id="item-run", name="running_plan")
        svc.engine._queue = [SimpleNamespace(id="item-q", name="queued_plan")]
        reply = _invoke(svc, "commands.queue.get", {})
        assert {"item_id": "item-run", "plan_name": "running_plan", "state": "running"} in reply["items"]
        assert {"item_id": "item-q", "plan_name": "queued_plan", "state": "queued"} in reply["items"]

    def test_actions_are_trusted(self, svc):
        for suffix in ("commands.engine.status", "commands.queue.get"):
            assert suffix in svc.ipc._trusted_actions


class _FakeRegistry:
    """Mimics PlanRegistry for plan.list / plan.run."""

    def __init__(self, plans):
        self._plans = plans

    def get_plan(self, name):
        return self._plans.get(name)

    def list_plans(self):
        return list(self._plans.values())


@pytest.fixture
def plan_svc(qapp, monkeypatch):
    # invoke_in_main_thread's QObject invoker must be created on the main
    # thread before any background thread posts to it (Qt event delivery
    # is thread-affine); production does this at app startup.
    from lightfall.utils.threads import initialize_main_thread_invoker

    initialize_main_thread_invoker()

    ipc, sent = _make_ipc()
    engine = _FakeEngine()

    from typing import Annotated

    from lightfall.ui.annotations import Unit

    def count(num: Annotated[int, Unit("pts")] = 5, delay: float = 0.0):
        yield from ()

    plan_info = SimpleNamespace(
        name="count",
        func=count,
        description="Count sim",
        parameters=[
            SimpleNamespace(
                name="num", annotation=count.__annotations__["num"], default=5, required=False
            ),
            SimpleNamespace(
                name="delay", annotation=float, default=0.0, required=False
            ),
        ],
    )
    registry = _FakeRegistry({"count": plan_info})
    import lightfall.remote.service as service_mod

    monkeypatch.setattr(service_mod, "_get_plan_registry", lambda: registry)

    service = RemoteControlService(ipc, engine=engine, catalog=None)
    service.start()
    yield SimpleNamespace(ipc=ipc, sent=sent, engine=engine, service=service)
    service.stop()


class TestPlanList:
    def test_lists_plans_with_param_metadata(self, plan_svc):
        reply = _invoke(plan_svc, "commands.plan.list", {})
        assert reply["contract_version"] == 1
        plans = {p["name"]: p for p in reply["plans"]}
        assert "count" in plans
        params = {p["name"]: p for p in plans["count"]["params"]}
        assert params["num"]["type"] == "int"
        assert params["num"]["unit"] == "pts"
        assert params["num"]["default"] == 5
        assert params["delay"]["type"] == "float"
        assert params["delay"]["unit"] is None


class TestPlanRun:
    def test_reject_default_when_busy(self, plan_svc):
        plan_svc.engine.is_idle = False
        reply = _invoke(plan_svc, "commands.plan.run", {"plan_name": "count", "params": {}})
        assert reply["status"] == "error"
        assert reply["code"] == "busy"
        assert plan_svc.engine.submitted == []

    def test_queue_behavior_submits_when_busy(self, plan_svc):
        plan_svc.engine.is_idle = False
        reply = _invoke(
            plan_svc,
            "commands.plan.run",
            {"plan_name": "count", "params": {}, "behavior": "queue"},
        )
        assert reply["status"] == "submitted"
        assert reply["item_id"] == "item-1"
        assert reply["run_uid"] is None  # queued: no start doc yet

    def test_submit_idle_fills_run_uid_from_start_doc(self, plan_svc):
        import threading

        def emit_start_soon():
            time.sleep(0.1)
            plan_svc.engine._current = SimpleNamespace(id="item-1", name="count")
            from lightfall.utils.threads import invoke_in_main_thread

            invoke_in_main_thread(
                plan_svc.engine.sigOutput.emit, "start", {"uid": "uid-9", "plan_name": "count"}
            )

        threading.Thread(target=emit_start_soon, daemon=True).start()
        reply = _invoke(plan_svc, "commands.plan.run", {"plan_name": "count", "params": {}})
        assert reply["status"] == "submitted"
        assert reply["item_id"] == "item-1"
        assert reply["run_uid"] == "uid-9"

    def test_missing_plan_name_bad_request(self, plan_svc):
        reply = _invoke(plan_svc, "commands.plan.run", {"params": {}})
        assert reply["code"] == "bad_request"

    def test_unknown_plan(self, plan_svc):
        reply = _invoke(plan_svc, "commands.plan.run", {"plan_name": "nope", "params": {}})
        assert reply["code"] == "unknown"

    def test_bad_behavior_value(self, plan_svc):
        reply = _invoke(
            plan_svc, "commands.plan.run", {"plan_name": "count", "behavior": "yolo"}
        )
        assert reply["code"] == "bad_request"

    def test_bad_params_bad_request(self, plan_svc):
        reply = _invoke(
            plan_svc, "commands.plan.run", {"plan_name": "count", "params": {"nope": 1}}
        )
        assert reply["code"] == "bad_request"


class TestPlanAbort:
    def test_abort_requested(self, plan_svc):
        reply = _invoke(plan_svc, "commands.plan.abort", {"reason": "operator"})
        assert reply == {"status": "abort_requested", "contract_version": 1}

    def test_nothing_to_abort(self, plan_svc):
        plan_svc.engine.abort = lambda reason="": False
        reply = _invoke(plan_svc, "commands.plan.abort", {})
        assert reply["status"] == "not_aborted"
        assert "message" in reply
