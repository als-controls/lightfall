import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.models import Observation
from lightfall.monitor.registry import MonitorRegistry
from lightfall.monitor.scheduler import MonitorScheduler


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _FakeEngine:
    def __init__(self):
        self._cb = None
        self.sigAbort = _Sig(); self.sigException = _Sig()
    def subscribe(self, cb):
        self._cb = cb; return 1
    def unsubscribe(self, token): self._cb = None
    def emit(self, name, doc):
        if self._cb: self._cb(name, doc)


class _Sig:  # minimal stand-in for a Qt signal
    def connect(self, *_a, **_k): pass


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
