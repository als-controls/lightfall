"""A paused plan is not a finished plan.

bluesky's ``RunEngine.__call__`` raises ``RunEngineInterrupted`` as soon as a
plan pauses. ``BlueskyEngine._execute_plan`` used to treat that like an abort:
"Run Aborted" toast, procedure cleared, ``sigProcedureFinished`` fired, and the
queue moved on to the next plan while the RunEngine was still paused. These
tests drive a real RunEngine through the engine and check that a pause holds
the queue until resume()/stop() settles the plan, and that exactly one terminal
signal is emitted for it.
"""

from __future__ import annotations

import time

import bluesky.plan_stubs as bps
import pytest

from lightfall.acquire.engine import BlueskyEngine, EngineState


def _pump(qapp, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _pump_until(qapp, condition, timeout: float = 10.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.01)
    pytest.fail(f"timed out waiting for {what}")


def pausing_plan(pauses: int = 1):
    """Open a run, pause ``pauses`` times (each at its own checkpoint), close."""
    yield from bps.open_run()
    for _ in range(pauses):
        yield from bps.checkpoint()
        yield from bps.pause()
    yield from bps.close_run()


def trivial_plan():
    yield from bps.open_run()
    yield from bps.close_run()


class _Recorder:
    """Collects the engine's broad and per-procedure signals."""

    def __init__(self, engine: BlueskyEngine) -> None:
        self.finish: list[None] = []
        self.abort: list[None] = []
        self.exception: list[BaseException] = []
        self.ready: list[None] = []
        self.pause: list[None] = []
        self.procedure_finished: list[tuple[str, object]] = []
        self.completed: list[bool] = []
        engine.sigFinish.connect(lambda: self.finish.append(None))
        engine.sigAbort.connect(lambda: self.abort.append(None))
        engine.sigException.connect(self.exception.append)
        engine.sigReady.connect(lambda: self.ready.append(None))
        engine.sigPause.connect(lambda: self.pause.append(None))
        engine.sigProcedureFinished.connect(
            lambda pid, err: self.procedure_finished.append((pid, err))
        )


@pytest.fixture
def engine(qapp):
    eng = BlueskyEngine(toast_notifications=False)
    _pump_until(qapp, lambda: eng.RE is not None, what="RunEngine creation")
    yield eng
    # Never leave a paused RunEngine behind: the queue thread would wait on it.
    if eng.RE is not None and eng.RE.state == "paused":
        eng.RE.abort()
        _pump_until(qapp, lambda: eng.RE.state == "idle", what="abort cleanup")
    if eng._queue_future is not None:
        eng._queue_future.cancel(timeout_ms=3000)


def _submit_and_pause(engine, qapp, rec, pauses: int = 1) -> str:
    pid = engine.submit(pausing_plan(pauses), name="pausing", on_complete=rec.completed.append)
    _pump_until(qapp, lambda: engine.RE.state == "paused", what="pause")
    return pid


def test_pause_holds_the_plan_instead_of_aborting(engine, qapp):
    rec = _Recorder(engine)
    _submit_and_pause(engine, qapp, rec)
    _pump(qapp, 0.5)

    assert rec.abort == [], "a pause must not be reported as an abort"
    assert rec.procedure_finished == [], "a paused plan has not finished"
    assert rec.completed == []
    assert engine._current_procedure is not None
    assert engine.state == EngineState.PAUSED
    assert len(rec.pause) == 1


def test_resume_finishes_the_paused_plan_once(engine, qapp):
    rec = _Recorder(engine)
    pid = _submit_and_pause(engine, qapp, rec)

    engine.resume()
    _pump_until(qapp, lambda: rec.procedure_finished, what="plan completion")
    _pump(qapp, 0.3)

    assert rec.procedure_finished == [(pid, None)]
    assert len(rec.finish) == 1
    assert rec.abort == []
    assert rec.exception == []
    assert rec.completed == [True]
    assert engine.RE.state == "idle"
    assert engine._current_procedure is None


def test_stop_from_paused_settles_as_aborted_and_signals_ready(engine, qapp):
    rec = _Recorder(engine)
    pid = _submit_and_pause(engine, qapp, rec)

    assert engine.stop() is True
    _pump_until(qapp, lambda: rec.procedure_finished, what="plan completion")
    _pump(qapp, 0.3)

    assert rec.procedure_finished == [(pid, None)]
    assert len(rec.abort) == 1
    assert rec.finish == []
    assert rec.exception == []
    assert rec.completed == [False]
    assert rec.ready, "sigReady must follow a plan stopped from paused"
    assert engine.RE.state == "idle"


def test_abort_from_paused_emits_abort_exactly_once(engine, qapp):
    rec = _Recorder(engine)
    pid = _submit_and_pause(engine, qapp, rec)

    assert engine.abort("test") is True
    _pump_until(qapp, lambda: rec.procedure_finished, what="plan completion")
    _pump(qapp, 0.3)

    assert rec.procedure_finished == [(pid, None)]
    assert len(rec.abort) == 1
    assert rec.exception == []


def test_queued_plan_waits_for_the_paused_one(engine, qapp):
    rec = _Recorder(engine)
    pid1 = _submit_and_pause(engine, qapp, rec)
    pid2 = engine.submit(trivial_plan(), name="second")
    _pump(qapp, 0.5)

    assert rec.procedure_finished == [], "nothing may run while the RunEngine is paused"
    assert rec.exception == []

    engine.resume()
    _pump_until(qapp, lambda: len(rec.procedure_finished) == 2, what="both plans")

    assert [pid for pid, _ in rec.procedure_finished] == [pid1, pid2]
    assert all(err is None for _, err in rec.procedure_finished)
    assert rec.exception == []


def test_second_pause_after_resume_is_not_an_error(engine, qapp):
    rec = _Recorder(engine)
    pid = _submit_and_pause(engine, qapp, rec, pauses=2)

    engine.resume()
    _pump_until(qapp, lambda: len(rec.pause) == 2, what="second pause")
    _pump(qapp, 0.3)

    assert engine.RE.state == "paused"
    assert rec.exception == [], "a re-pause must not surface as sigException"
    assert rec.procedure_finished == []

    engine.resume()
    _pump_until(qapp, lambda: rec.procedure_finished, what="plan completion")
    assert rec.procedure_finished == [(pid, None)]
    assert len(rec.finish) == 1
    assert rec.abort == []
