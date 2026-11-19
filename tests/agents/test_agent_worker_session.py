"""AgentWorker session-stream mode: busy/idle from turn boundaries + prompt echoes.

The scripted sequences mirror what Claude Code 2.1.318 was observed to send
(2026-11-19): injected task-notification turns, prompts joining a running
turn, interrupts that leave a queued prompt to run next.
"""
from __future__ import annotations

import asyncio

import pytest

from lightfall.agents import worker as worker_mod
from lightfall.agents.backend import (
    PromptAcceptedEvent,
    TextBlockEvent,
    TurnErrorEvent,
    TurnResultEvent,
    TurnStartedEvent,
)
from lightfall.agents.worker import AgentWorker

_END = object()


def _ours(**info):
    return TurnResultEvent({"origin": "", "cancelled": False, **info})


def _injected():
    return TurnResultEvent({"origin": "task-notification", "cancelled": False})


class _SessionBackend:
    """Scripted session stream. ``replies`` maps each sent prompt to events."""

    session_stream = True

    def __init__(self, replies=None, on_interrupt=()):
        self.replies = replies or {}
        self.on_interrupt = list(on_interrupt)
        self.sent: list[str] = []
        self.interrupts = 0
        self.models: list = []
        self._q: asyncio.Queue | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    async def connect(self):
        self._q = asyncio.Queue()
        self.loop = asyncio.get_running_loop()

    async def send(self, prompt):
        self.sent.append(prompt)
        for ev in self.replies.get(prompt, ()):
            self._q.put_nowait(ev)

    async def events(self, stopping):
        while not stopping():
            ev = await self._q.get()
            if ev is _END:
                return
            yield ev

    def push(self, *events):
        """Inject events from the test thread (e.g. an unprompted turn)."""
        for ev in events:
            self.loop.call_soon_threadsafe(self._q.put_nowait, ev)

    async def interrupt(self):
        self.interrupts += 1
        for ev in self.on_interrupt:
            self._q.put_nowait(ev)

    async def set_model(self, model):
        self.models.append(model)

    async def set_effort(self, effort):
        pass

    async def close(self):
        pass


@pytest.fixture
def make_worker(qtbot):
    workers = []

    def make(backend):
        w = AgentWorker(backend)
        with qtbot.waitSignal(w.connected, timeout=5000):
            w.start()
        workers.append(w)
        return w

    yield make
    for w in workers:
        w.stop()
        assert w.wait(3000)


def _record(w):
    log = []
    w.query_completed.connect(lambda: log.append("completed"))
    w.query_cancelled.connect(lambda: log.append("cancelled"))
    w.error_occurred.connect(lambda m: log.append(f"error:{m}"))
    w.message_received.connect(lambda t: log.append(f"text:{t}"))
    w.turn_started.connect(lambda unprompted: log.append(f"turn:{unprompted}"))
    w.result_received.connect(lambda info: log.append(f"result:{info.get('origin')}"))
    return log


def test_injected_turn_at_send_does_not_end_the_prompt(qtbot, make_worker):
    """The reported bug: a pending task notification made the CLI close an
    injected turn first; the UI went idle and lost the real turn."""
    b = _SessionBackend(replies={"hi": [
        TurnStartedEvent(), _injected(),
        TurnStartedEvent(), PromptAcceptedEvent(), TextBlockEvent("answer"), _ours(),
    ]})
    w = make_worker(b)
    log = _record(w)
    with qtbot.waitSignal(w.query_completed, timeout=3000):
        w.send_query("hi")
    assert log == [
        "turn:False", "result:task-notification",
        "turn:False", "text:answer", "result:", "completed",
    ]
    assert not w.is_processing


def test_busy_spans_the_injected_turn(qtbot, make_worker):
    b = _SessionBackend(replies={"hi": [TurnStartedEvent(), _injected()]})
    w = make_worker(b)
    log = _record(w)
    w.send_query("hi")
    qtbot.waitUntil(lambda: "result:task-notification" in log, timeout=3000)
    assert w.is_processing  # our prompt has not been taken in yet
    assert "completed" not in log
    b.push(TurnStartedEvent(), PromptAcceptedEvent(), _ours())
    qtbot.waitUntil(lambda: "completed" in log, timeout=3000)
    assert not w.is_processing


def test_unprompted_turn_while_idle_is_surfaced(qtbot, make_worker):
    """A background task finishing while idle starts a turn with real output;
    it must reach the UI now, not ride along with the next prompt."""
    b = _SessionBackend()
    w = make_worker(b)
    log = _record(w)
    with qtbot.waitSignal(w.query_completed, timeout=3000):
        b.push(TurnStartedEvent(), TextBlockEvent("bg done"), _injected())
    assert log == ["turn:True", "text:bg done", "result:task-notification", "completed"]
    assert not w.is_processing


def test_prompt_sent_mid_turn_completes_once(qtbot, make_worker):
    b = _SessionBackend(replies={"A": [TurnStartedEvent(), PromptAcceptedEvent()]})
    w = make_worker(b)
    log = _record(w)
    w.send_query("A")
    qtbot.waitUntil(lambda: "turn:False" in log, timeout=3000)
    w.send_query("B")
    qtbot.waitUntil(lambda: b.sent == ["A", "B"], timeout=3000)
    b.push(PromptAcceptedEvent(), TextBlockEvent("B-DONE"), _ours())
    qtbot.waitUntil(lambda: "completed" in log, timeout=3000)
    assert log.count("completed") == 1
    assert not w.is_processing


def test_is_busy_as_soon_as_a_prompt_is_queued(qtbot, make_worker):
    w = make_worker(_SessionBackend())
    w.send_query("hi")
    assert w.is_processing  # before the loop has even sent it


def test_cancel_drops_rest_of_turn_and_emits_cancelled_once(qtbot, make_worker):
    b = _SessionBackend(
        replies={"go": [TurnStartedEvent(), PromptAcceptedEvent(), TextBlockEvent("part")]},
        on_interrupt=[TextBlockEvent("late"), TurnResultEvent({"origin": "", "cancelled": True})],
    )
    w = make_worker(b)
    log = _record(w)
    w.send_query("go")
    qtbot.waitUntil(lambda: "text:part" in log, timeout=3000)
    with qtbot.waitSignal(w.query_cancelled, timeout=3000):
        assert w.cancel_current_query() is True
    assert b.interrupts == 1
    assert "text:late" not in log
    assert log.count("cancelled") == 1 and "completed" not in log
    assert not w.is_processing


def test_prompt_queued_behind_a_cancelled_turn_runs_next(qtbot, make_worker):
    """interrupt() aborts only the running turn; a queued prompt then gets its own."""
    b = _SessionBackend(
        replies={"A": [TurnStartedEvent(), PromptAcceptedEvent()]},
        on_interrupt=[TurnResultEvent({"origin": "", "cancelled": True})],
    )
    w = make_worker(b)
    log = _record(w)
    w.send_query("A")
    qtbot.waitUntil(lambda: "turn:False" in log, timeout=3000)
    w.send_query("B")
    qtbot.waitUntil(lambda: b.sent == ["A", "B"], timeout=3000)
    with qtbot.waitSignal(w.query_cancelled, timeout=3000):
        w.cancel_current_query()
    assert w.is_processing  # B is still waiting
    b.push(TurnStartedEvent(), PromptAcceptedEvent(), TextBlockEvent("B"), _ours())
    qtbot.waitUntil(lambda: "completed" in log, timeout=3000)
    assert log[-3:] == ["text:B", "result:", "completed"]


def test_cancel_without_result_settles_and_drops_late_result(qtbot, make_worker, monkeypatch):
    monkeypatch.setattr(worker_mod, "CANCEL_SETTLE_S", 0.3)
    b = _SessionBackend(replies={"go": [TurnStartedEvent(), PromptAcceptedEvent()]})
    w = make_worker(b)
    log = _record(w)
    w.send_query("go")
    qtbot.waitUntil(lambda: "turn:False" in log, timeout=3000)
    with qtbot.waitSignal(w.query_cancelled, timeout=3000):
        w.cancel_current_query()
    assert not w.is_processing
    b.push(TextBlockEvent("late"), _ours())  # the abandoned turn finally ends
    qtbot.wait(300)
    assert "text:late" not in log and "completed" not in log
    # The next turn renders normally.
    with qtbot.waitSignal(w.query_completed, timeout=3000):
        b.push(TurnStartedEvent(), TextBlockEvent("next"), _injected())
    assert "text:next" in log


def test_prompt_never_taken_in_gives_up(qtbot, make_worker, monkeypatch):
    monkeypatch.setattr(worker_mod, "PROMPT_STALL_S", 0.3)
    w = make_worker(_SessionBackend())
    with qtbot.waitSignal(w.error_occurred, timeout=3000):
        w.send_query("hello?")
    assert not w.is_processing


def test_stream_end_reports_error_and_later_sends_fail(qtbot, make_worker):
    b = _SessionBackend()
    w = make_worker(b)
    log = _record(w)
    with qtbot.waitSignal(w.error_occurred, timeout=3000):
        b.push(_END)
    with qtbot.waitSignal(w.error_occurred, timeout=3000):
        w.send_query("anyone?")
    assert b.sent == []
    assert not w.is_processing
    assert sum(e.startswith("error:") for e in log) == 2


def test_backend_error_event_resets_busy(qtbot, make_worker):
    b = _SessionBackend(replies={"hi": [TurnStartedEvent(), TurnErrorEvent("boom")]})
    w = make_worker(b)
    with qtbot.waitSignal(w.error_occurred, timeout=3000) as sig:
        w.send_query("hi")
    assert sig.args == ["boom"]
    qtbot.waitUntil(lambda: not w.is_processing, timeout=3000)


def test_set_model_during_turn_applies_when_idle(qtbot, make_worker):
    b = _SessionBackend(replies={"hi": [TurnStartedEvent(), PromptAcceptedEvent()]})
    w = make_worker(b)
    log = _record(w)
    w.send_query("hi")
    qtbot.waitUntil(lambda: "turn:False" in log, timeout=3000)
    w.request_set_model("haiku")
    qtbot.wait(200)
    assert b.models == []
    with qtbot.waitSignal(w.query_completed, timeout=3000):
        b.push(_ours())
    qtbot.waitUntil(lambda: b.models == ["haiku"], timeout=3000)
