"""AgentWorker awaits backend.close() once when the worker stops."""
from __future__ import annotations

from lightfall.agents.backend import TurnResultEvent
from lightfall.agents.worker import AgentWorker


class _Backend:
    def __init__(self, fail=False):
        self.closed = 0
        self.fail = fail

    async def connect(self):
        pass

    async def run_turn(self, prompt, cancelled, stopping):
        yield TurnResultEvent({})

    async def interrupt(self):
        pass

    async def set_model(self, model):
        pass

    async def close(self):
        self.closed += 1
        if self.fail:
            raise RuntimeError("boom")


def test_close_called_once_on_stop(qtbot):
    b = _Backend()
    w = AgentWorker(b)
    with qtbot.waitSignal(w.connected, timeout=5000):
        w.start()
    w.stop()
    assert w.wait(5000)
    assert b.closed == 1


def test_close_failure_does_not_crash_worker(qtbot):
    b = _Backend(fail=True)
    w = AgentWorker(b)
    errors = []
    w.error_occurred.connect(errors.append)
    with qtbot.waitSignal(w.connected, timeout=5000):
        w.start()
    w.stop()
    assert w.wait(5000)
    assert b.closed == 1
    assert errors == []
