import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.models import Observation
from lightfall.monitor.registry import MonitorRegistry
from lightfall.monitor.scheduler import MonitorScheduler


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _Sig:  # minimal stand-in for a Qt signal
    def __init__(self):
        self._slots: list = []

    def connect(self, slot, *_a, **_k):
        self._slots.append(slot)

    def disconnect(self, slot):
        self._slots.remove(slot)

    def connection_count(self) -> int:
        return len(self._slots)


class _FakeEngine:
    def __init__(self):
        self._cb = None
        self.sigAbort = _Sig(); self.sigException = _Sig()
    def subscribe(self, cb):
        self._cb = cb; return 1
    def unsubscribe(self, token): self._cb = None
    def emit(self, name, doc):
        if self._cb: self._cb(name, doc)


class _CountFeed(MonitorFeed):
    name = "count"
    default_interval_s = 0.0  # always due
    def evaluate(self, ctx, window, prior):
        if window.latest("det") == 0:
            return Observation(severity="warn", feed_name=self.name,
                               run_uid=window.run_uid, title="zero",
                               message="det=0", state_key="count:zero")
        return None


@pytest.fixture
def _registry():
    MonitorRegistry.reset_instance()
    reg = MonitorRegistry.get_instance()

    class _P(  # noqa: N801
        __import__("lightfall.monitor.monitor_plugin", fromlist=["MonitorPlugin"]).MonitorPlugin
    ):
        @property
        def name(self): return "count_plugin"
        @property
        def description(self): return "d"
        def create_feeds(self): return [_CountFeed()]

    reg.register(_P())
    reg._read_list_pref = lambda key: []  # no prefs in test
    yield reg
    MonitorRegistry.reset_instance()


def test_scheduler_emits_rate_limited_observation(_app, _registry):
    eng = _FakeEngine()
    t = [0.0]
    sched = MonitorScheduler(eng, registry=_registry, clock=lambda: t[0], eval_async=False)
    received = []
    sched.observation.connect(received.append)
    sched.start()

    eng.emit("start", {"uid": "u1", "time": 0.0})
    eng.emit("event", {"seq_num": 1, "time": 0.0, "data": {"det": 0}})
    sched._tick()  # would be the QTimer; called directly here
    sched._tick()  # second tick: same condition -> suppressed by rate limiter

    assert len(received) == 1
    assert received[0].state_key == "count:zero"


def test_disarm_on_stop_stops_emitting(_app, _registry):
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, clock=lambda: 0.0, eval_async=False)
    received = []
    sched.observation.connect(received.append)
    sched.start()
    eng.emit("start", {"uid": "u1", "time": 0.0})
    eng.emit("stop", {"run_start": "u1"})
    sched._tick()
    assert received == []  # disarmed


def test_start_stop_cycle_no_duplicate_connections(_app, _registry):
    """start()/stop() must not accumulate sigAbort/sigException connections."""
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, clock=lambda: 0.0, eval_async=False)
    sched.start()
    assert eng.sigAbort.connection_count() == 1
    assert eng.sigException.connection_count() == 1
    sched.stop()
    # After stop() the connections are removed.
    assert eng.sigAbort.connection_count() == 0
    assert eng.sigException.connection_count() == 0
    # Second start/stop cycle must still give exactly one connection each.
    sched.start()
    assert eng.sigAbort.connection_count() == 1
    assert eng.sigException.connection_count() == 1
    sched.stop()
    assert eng.sigAbort.connection_count() == 0
    assert eng.sigException.connection_count() == 0


def test_stop_disconnects_signals_so_disarm_not_called_after_stop(_app, _registry):
    """After stop(), an engine abort must not trigger _disarm on this scheduler."""
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, clock=lambda: 0.0, eval_async=False)
    sched.start()
    eng.emit("start", {"uid": "u1", "time": 0.0})
    sched.stop()
    # Manually fire all remaining slots — there should be none for sigAbort.
    disarm_called = []
    original_disarm = sched._disarm
    sched._disarm = lambda: disarm_called.append(1) or original_disarm()
    for slot in list(eng.sigAbort._slots):
        slot()
    assert disarm_called == [], "_disarm fired after stop() — signal not disconnected"
