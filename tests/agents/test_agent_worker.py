"""AgentWorker maps neutral AgentEvents to the legacy Qt signals."""
from __future__ import annotations

import asyncio

from lightfall.agents.backend import (
    ContextUsageEvent,
    SessionIdEvent,
    TextBlockEvent,
    TurnCancelledEvent,
    TurnResultEvent,
)
from lightfall.agents.worker import AgentWorker


class _FakeBackend:
    def __init__(self, events):
        self.events = events
        self.interrupted = 0

    async def connect(self):
        pass

    async def run_turn(self, prompt, cancelled, stopping):
        for ev in self.events:
            await asyncio.sleep(0)
            yield ev

    async def interrupt(self):
        self.interrupted += 1

    async def set_model(self, model):
        pass


def _run(qtbot, worker, prompt="hi", wait_signal=None):
    with qtbot.waitSignal(worker.connected, timeout=5000):
        worker.start()
    with qtbot.waitSignal(wait_signal or worker.query_completed, timeout=5000):
        worker.send_query(prompt)


def test_text_block_maps_to_message_received(qtbot):
    w = AgentWorker(_FakeBackend([TextBlockEvent("hello"), TurnResultEvent({"x": 1})]))
    got = []
    w.message_received.connect(got.append)
    try:
        _run(qtbot, w)
        assert got == ["hello"]
    finally:
        w.stop()
        w.wait(3000)


def test_turn_result_signal_order(qtbot):
    events = [
        TurnResultEvent({"total_cost_usd": 0.1, "session_id": "s1"}),
        SessionIdEvent("s1"),
        ContextUsageEvent({"totalTokens": 5}),
    ]
    w = AgentWorker(_FakeBackend(events))
    order = []
    w.result_received.connect(lambda d: order.append("result"))
    w.query_completed.connect(lambda: order.append("completed"))
    w.session_id_changed.connect(lambda s: order.append(f"sid:{s}"))
    w.context_usage.connect(lambda d: order.append("ctx"))
    try:
        _run(qtbot, w, wait_signal=w.context_usage)
        assert order == ["result", "completed", "sid:s1", "ctx"]
    finally:
        w.stop()
        w.wait(3000)


def test_worker_cancel_emits_cancelled_once(qtbot):
    w = AgentWorker(_FakeBackend([TurnCancelledEvent()]))
    cancelled, completed = [], []
    w.query_cancelled.connect(lambda: cancelled.append(1))
    w.query_completed.connect(lambda: completed.append(1))
    try:
        _run(qtbot, w, wait_signal=w.query_cancelled)
        assert cancelled == [1] and completed == []
    finally:
        w.stop()
        w.wait(3000)


class _SlowBackend(_FakeBackend):
    def __init__(self):
        super().__init__([])
        self.models = []

    async def run_turn(self, prompt, cancelled, stopping):
        yield TextBlockEvent("first")
        await asyncio.sleep(0.3)
        yield TurnResultEvent({})

    async def set_model(self, model):
        self.models.append(model)


def test_set_model_during_turn_applies_after(qtbot):
    backend = _SlowBackend()
    w = AgentWorker(backend)
    w.message_received.connect(lambda _t: w.request_set_model("m2"))
    try:
        _run(qtbot, w)
        qtbot.waitUntil(lambda: backend.models == ["m2"], timeout=3000)
    finally:
        w.stop()
        w.wait(3000)


class _SlowConnectBackend(_FakeBackend):
    def __init__(self):
        super().__init__([])
        self.connected = False
        self.models = []  # (model, connected-at-the-time)

    async def connect(self):
        await asyncio.sleep(0.3)
        self.connected = True

    async def set_model(self, model):
        self.models.append((model, self.connected))


def test_set_model_while_connecting_applies_after_connect(qtbot):
    backend = _SlowConnectBackend()
    w = AgentWorker(backend)
    try:
        with qtbot.waitSignal(w.connected, timeout=5000):
            w.start()
            w.request_set_model("m2")  # before connect() has finished
        qtbot.waitUntil(lambda: backend.models == [("m2", True)], timeout=3000)
    finally:
        w.stop()
        w.wait(3000)


class _SlowConnectEffortBackend(_SlowConnectBackend):
    def __init__(self):
        super().__init__()
        self.efforts = []  # (effort, connected-at-the-time)

    async def set_effort(self, effort):
        self.efforts.append((effort, self.connected))


def test_set_effort_while_connecting_applies_after_connect(qtbot):
    backend = _SlowConnectEffortBackend()
    w = AgentWorker(backend)
    try:
        with qtbot.waitSignal(w.connected, timeout=5000):
            w.start()
            w.request_set_effort("high")  # before connect() has finished
        qtbot.waitUntil(lambda: backend.efforts == [("high", True)], timeout=3000)
    finally:
        w.stop()
        w.wait(3000)
