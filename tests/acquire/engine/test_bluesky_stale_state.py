"""An exception escaping bluesky's ``_run`` cleanup must not wedge the engine.

When Lightfall runs without a console (pythonw / ``lightfall-gui``),
``sys.stdout`` is ``None`` and bluesky's ``RunEngine._run`` finally-block
``sys.stdout.flush()`` raises ``AttributeError`` *after* the run completed but
*before* it resets its state to "idle". ``BlueskyEngine._execute_plan`` then
mapped the stale "running" state to ``EngineState.RUNNING`` and the queue was
stuck. The engine must recover the RunEngine to idle and keep processing.
"""

from __future__ import annotations

import sys

from lightfall.acquire.engine import EngineState

from .test_bluesky_pause import _pump, _pump_until, _Recorder, engine, trivial_plan  # noqa: F401


def test_exception_in_run_cleanup_does_not_leave_engine_running(engine, qapp, monkeypatch):  # noqa: F811
    rec = _Recorder(engine)
    monkeypatch.setattr(sys, "stdout", None)

    pid = engine.submit(trivial_plan(), name="no-stdout")
    _pump_until(qapp, lambda: rec.procedure_finished, what="plan completion")
    _pump(qapp, 0.2)

    assert rec.procedure_finished[0][0] == pid
    assert isinstance(rec.procedure_finished[0][1], AttributeError)
    assert engine.RE.state == "idle"
    assert engine.state == EngineState.IDLE

    # The queue keeps working afterwards.
    monkeypatch.undo()
    pid2 = engine.submit(trivial_plan(), name="after")
    _pump_until(qapp, lambda: len(rec.procedure_finished) == 2, what="second plan")
    assert rec.procedure_finished[1] == (pid2, None)
    assert engine.RE.state == "idle"
    assert engine.state == EngineState.IDLE


def test_recover_is_a_noop_when_run_engine_is_idle(engine):  # noqa: F811
    assert engine.RE.state == "idle"
    engine._recover_stale_run_engine(RuntimeError("boom"))
    assert engine.RE.state == "idle"
