import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.registry import MonitorRegistry
from lightfall.monitor.scheduler import MonitorScheduler


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _Sig:
    def __init__(self):
        self._slots: list = []

    def connect(self, slot, *_a, **_k):
        self._slots.append(slot)

    def disconnect(self, slot):
        self._slots.remove(slot)


class _FakeEngine:
    def __init__(self):
        self._cb = None
        self.sigAbort = _Sig(); self.sigException = _Sig()

    def subscribe(self, cb):
        self._cb = cb; return 1

    def unsubscribe(self, token): self._cb = None

    def emit(self, name, doc):
        if self._cb: self._cb(name, doc)


def _make_feed(name, interval, counter):
    def evaluate(self, ctx, window, prior):
        counter[name] = counter.get(name, 0) + 1
        return None

    _Feed = type("_Feed", (MonitorFeed,), {
        "name": name,
        "default_interval_s": interval,
        "evaluate": evaluate,
    })
    return _Feed()


def _register(*feeds):
    MonitorRegistry.reset_instance()
    reg = MonitorRegistry.get_instance()

    class _P(  # noqa: N801
        __import__("lightfall.monitor.monitor_plugin", fromlist=["MonitorPlugin"]).MonitorPlugin
    ):
        @property
        def name(self): return "test_plugin"
        @property
        def description(self): return "d"
        def create_feeds(self): return list(feeds)

    reg.register(_P())
    reg._read_list_pref = lambda key: []
    return reg


def _arm(sched, eng, uid="u1"):
    eng.emit("start", {"uid": uid, "time": 0.0})


def test_disabled_feed_never_dispatched(_app, monkeypatch):
    counter = {}
    feed = _make_feed("f1", 0.0, counter)
    reg = _register(feed)
    eng = _FakeEngine()
    t = [0.0]
    sched = MonitorScheduler(eng, registry=reg, clock=lambda: t[0], eval_async=False)
    monkeypatch.setattr(sched, "_feed_config", lambda: ({"f1"}, {}))
    sched.start()
    _arm(sched, eng)
    sched._tick()
    sched._tick()
    assert counter.get("f1", 0) == 0
    MonitorRegistry.reset_instance()


def test_interval_override_shortens_gate(_app, monkeypatch):
    counter = {}
    feed = _make_feed("f2", 30.0, counter)
    reg = _register(feed)
    eng = _FakeEngine()
    t = [0.0]
    sched = MonitorScheduler(eng, registry=reg, clock=lambda: t[0], eval_async=False)
    monkeypatch.setattr(sched, "_feed_config", lambda: (set(), {"f2": 5.0}))
    sched.start()
    _arm(sched, eng)
    sched._tick()
    assert counter.get("f2", 0) == 1
    t[0] = 6.0
    sched._tick()
    assert counter.get("f2", 0) == 2
    MonitorRegistry.reset_instance()


def test_override_below_one_second_clamps(_app, monkeypatch):
    counter = {}
    feed = _make_feed("f3", 30.0, counter)
    reg = _register(feed)
    eng = _FakeEngine()
    t = [0.0]
    sched = MonitorScheduler(eng, registry=reg, clock=lambda: t[0], eval_async=False)
    monkeypatch.setattr(sched, "_feed_config", lambda: (set(), {"f3": 0.1}))
    sched.start()
    _arm(sched, eng)
    sched._tick()
    assert counter.get("f3", 0) == 1
    t[0] = 0.5  # below clamp floor of 1.0s -> should not re-fire yet
    sched._tick()
    assert counter.get("f3", 0) == 1
    t[0] = 1.0
    sched._tick()
    assert counter.get("f3", 0) == 2
    MonitorRegistry.reset_instance()


def test_pref_read_failure_defaults_to_all_enabled(_app, monkeypatch):
    counter = {}
    feed = _make_feed("f4", 0.0, counter)
    reg = _register(feed)
    eng = _FakeEngine()
    t = [0.0]
    sched = MonitorScheduler(eng, registry=reg, clock=lambda: t[0], eval_async=False)

    def _boom():
        raise RuntimeError("prefs unavailable")

    monkeypatch.setattr("lightfall.monitor.scheduler._read_feed_config_prefs", _boom)
    sched.start()
    _arm(sched, eng)
    sched._tick()
    assert counter.get("f4", 0) == 1
    MonitorRegistry.reset_instance()
