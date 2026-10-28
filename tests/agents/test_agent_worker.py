"""AgentWorker maps neutral AgentEvents to the legacy Qt signals."""
from __future__ import annotations

import asyncio
import threading

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


class _GatedTurnBackend(_FakeBackend):
    """A turn that only ends once the test releases it (no timing assumptions)."""

    def __init__(self):
        super().__init__([])
        self.models = []  # (model, in-turn-at-the-time)
        self.release = threading.Event()
        self.in_turn = False

    async def run_turn(self, prompt, cancelled, stopping):
        self.in_turn = True
        yield TextBlockEvent("first")
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        self.in_turn = False
        yield TurnResultEvent({})

    async def set_model(self, model):
        self.models.append((model, self.in_turn))


def test_set_model_during_turn_applies_after(qtbot):
    backend = _GatedTurnBackend()
    w = AgentWorker(backend)
    requested = []
    w.message_received.connect(lambda _t: (w.request_set_model("m2"), requested.append(1)))
    try:
        with qtbot.waitSignal(w.connected, timeout=5000):
            w.start()
        w.send_query("hi")
        qtbot.waitUntil(lambda: requested == [1], timeout=3000)  # requested mid-turn
        qtbot.wait(100)
        assert backend.models == []  # held, not applied during the turn
        backend.release.set()
        qtbot.waitUntil(lambda: backend.models == [("m2", False)], timeout=3000)
    finally:
        backend.release.set()
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


def test_stream_ending_without_result_still_ends_the_turn(qtbot):
    """A backend whose stream closes mid-turn (the CLI exited) yields no
    terminal event; the worker must report it or the chat stays in its
    "processing" state forever."""
    w = AgentWorker(_FakeBackend([TextBlockEvent("partial")]))
    errors, completed = [], []
    w.error_occurred.connect(errors.append)
    w.query_completed.connect(lambda: completed.append(1))
    try:
        _run(qtbot, w, wait_signal=w.error_occurred)
        assert len(errors) == 1 and "without a result" in errors[0]
        assert completed == []
    finally:
        w.stop()
        w.wait(3000)
