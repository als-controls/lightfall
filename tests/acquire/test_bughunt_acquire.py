"""Regression tests for the 2026-10 acquire/ bug hunt (small fixes)."""

from __future__ import annotations

from bluesky import RunEngine

from lightfall.acquire.buffer import LiveDataBuffer
from lightfall.acquire.engine import BaseEngine, MockEngine
from lightfall.acquire.engine.console_proxy import ConsoleREProxy
from lightfall.acquire.engine.waiting_hook import WaitingHookBridge
from lightfall.acquire.plans.lightfall_plans import tune_centroid_2d

# --- MockEngine: per-procedure completion -----------------------------------


def test_mock_engine_emits_procedure_finished(qapp):
    engine = MockEngine()
    seen = []
    engine.sigProcedureFinished.connect(lambda pid, err: seen.append((pid, err)))

    uid = engine.submit(object(), name="mock")

    assert seen == [(uid, None)]


def test_console_proxy_returns_with_mock_engine(qapp):
    """ConsoleREProxy blocks on sigProcedureFinished; MockEngine never emitted
    it, so RE(plan) from the console hung forever in offline mode."""
    proxy = ConsoleREProxy(MockEngine())
    proxy(object())  # must return


# --- BaseEngine._emit_output: subscribers may change during emission ---------


class _Engine(BaseEngine):
    def pause(self, defer: bool = False) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> bool: return False
    def abort(self, reason: str = "") -> bool: return False
    def halt(self) -> bool: return False


def test_emit_output_survives_unsubscribe_from_callback(qapp):
    engine = _Engine(name="t", toast_notifications=False)
    calls = []

    def first(name, doc):
        calls.append("first")
        engine.unsubscribe(token_first)

    token_first = engine.subscribe(first)
    engine.subscribe(lambda name, doc: calls.append("second"))

    engine._emit_output("start", {})  # used to raise "dictionary changed size"

    assert calls == ["first", "second"]
    engine._emit_output("stop", {})
    assert calls == ["first", "second", "second"]


# --- LiveDataBuffer.is_running ----------------------------------------------


def test_live_buffer_is_running_tracks_start_and_stop(qapp):
    buf = LiveDataBuffer()
    assert buf.is_running is False

    buf("start", {"uid": "run-1", "time": 0.0, "plan_name": "count"})
    assert buf.is_running is True

    buf("stop", {"uid": "stop-1", "run_start": "run-1", "exit_status": "success"})
    assert buf.is_running is False
    assert buf.current_run is not None and buf.current_run.uid == "run-1"


# --- WaitingHookBridge: back-to-back wait groups in one flush window --------


class _Status:
    def __init__(self, name):
        self.name = name
        self.watchers = []
        self.callbacks = []

    def watch(self, cb):
        self.watchers.append(cb)

    def add_callback(self, cb):
        self.callbacks.append(cb)


def _progress(st, current):
    for cb in st.watchers:
        cb(current=current, initial=0.0, target=10.0)


def test_new_wait_group_in_same_flush_window_keeps_progress_flowing(qapp):
    bridge = WaitingHookBridge()
    events = []
    bridge.sigWaitGroupCleared.connect(lambda: events.append("cleared"))
    bridge.sigDeviceProgress.connect(lambda n, c, i, t, f: events.append(("progress", n, c)))

    first, second = _Status("m1"), _Status("m2")
    bridge({first})
    _progress(first, 9.0)
    bridge(None)            # group 1 resolved ...
    bridge({second})        # ... and group 2 started before the timer flushed
    _progress(second, 2.0)
    qapp.processEvents()    # deliver the queued timer.start()

    bridge._flush()

    # Clear comes first, then only the new group's progress; the timer keeps
    # running so later watch() updates for m2 are still flushed.
    assert events == ["cleared", ("progress", "m2", 2.0)]
    assert bridge._timer.isActive()

    events.clear()
    _progress(second, 5.0)
    bridge._flush()
    assert events == [("progress", "m2", 5.0)]

    bridge(None)
    bridge._flush()
    assert events[-1] == "cleared"
    assert not bridge._timer.isActive()


# --- tune_centroid_2d converges ----------------------------------------------


def test_tune_centroid_2d_stops_when_converged():
    from ophyd.sim import Syn2DGauss, motor1, motor2

    det = Syn2DGauss("det", motor1, "motor1", motor2, "motor2",
                     center=(0.8, -0.6), Imax=100.0, sigma=1.0)
    RE = RunEngine(context_managers=[])
    starts = []
    RE.subscribe(lambda name, doc: starts.append(doc), "start")

    motor1.set(-2.0)
    motor2.set(2.0)
    RE(tune_centroid_2d([det], "det", motor1, -3.0, 3.0, 0.05,
                        motor2, -3.0, 3.0, 0.05, num=10, max_iterations=10))

    # Two tune_centroid runs per pass; without a convergence check this is
    # always 2 * max_iterations = 20.
    assert len(starts) < 20
    assert abs(motor1.position - 0.8) < 0.2
    assert abs(motor2.position + 0.6) < 0.2
