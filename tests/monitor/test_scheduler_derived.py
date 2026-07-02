# tests/monitor/test_scheduler_derived.py
import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.registry import MonitorRegistry
from lightfall.monitor.scheduler import MonitorScheduler


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _FakeEngine:
    def __init__(self):
        self._cb = None
        self.sigAbort = _Sig(); self.sigException = _Sig()
    def subscribe(self, cb): self._cb = cb; return 1
    def unsubscribe(self, t): self._cb = None
    def emit(self, name, doc):
        if self._cb: self._cb(name, doc)


class _Sig:
    def connect(self, *a, **k): pass


class _Arr:
    def __init__(self, v): self._v = v
    def read(self): return self._v


class _Snap:
    def __init__(self, d): self._d = d
    def keys(self): return list(self._d.keys())
    def __getitem__(self, k): return _Arr(self._d[k])


class _Xpcs:
    def __init__(self, snaps): self._snaps = snaps  # {name: _Snap}
    def keys(self): return ["config", *self._snaps.keys()]
    def __getitem__(self, k):
        if k in self._snaps: return self._snaps[k]
        raise KeyError(k)


class _Run:
    def __init__(self, xpcs): self._xpcs = xpcs
    def __getitem__(self, k):
        if k == "xpcs" and self._xpcs is not None: return self._xpcs
        raise KeyError(k)


class _Client:
    def __init__(self, runs): self._runs = runs
    def __getitem__(self, uid):
        if uid in self._runs: return self._runs[uid]
        raise KeyError(uid)


class _Svc:
    def __init__(self, client): self._client = client
    @property
    def client(self): return self._client
    @property
    def is_connected(self): return self._client is not None


@pytest.fixture
def _registry():
    MonitorRegistry.reset_instance()
    reg = MonitorRegistry.get_instance()
    reg._read_list_pref = lambda key: []
    yield reg
    MonitorRegistry.reset_instance()


def _arm(sched, eng, uid):
    eng.emit("start", {"uid": uid, "time": 0.0})


def test_derived_returns_latest_snapshot(_app, _registry, monkeypatch):
    snap = _Snap({"tau": [1, 2], "g2_average": [1.3, 1.1], "g2_roi_0": [1.3, 1.1],
                  "frames_count": 50, "metrics_rms": [0.2], "intensity_average": [9.0]})
    client = _Client({"u1": _Run(_Xpcs({"snapshot_001": _Snap({}), "snapshot_002": snap}))})
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        staticmethod(lambda: _Svc(client)),
    )
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, eval_async=False)
    sched.start(); _arm(sched, eng, "u1")
    d = sched._derived("xpcs")
    assert d is not None
    assert d["snapshot"] == "snapshot_002"
    assert d["g2"]["average"] == [1.3, 1.1]
    assert d["g2"]["0"] == [1.3, 1.1]
    assert d["frames_count"] == 50


def test_derived_none_when_no_xpcs_stream(_app, _registry, monkeypatch):
    client = _Client({"u1": _Run(None)})  # run exists, no xpcs stream yet
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        staticmethod(lambda: _Svc(client)),
    )
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, eval_async=False)
    sched.start(); _arm(sched, eng, "u1")
    assert sched._derived("xpcs") is None


def test_derived_none_when_disconnected(_app, _registry, monkeypatch):
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        staticmethod(lambda: _Svc(None)),
    )
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, eval_async=False)
    sched.start(); _arm(sched, eng, "u1")
    assert sched._derived("xpcs") is None


def test_derived_non_xpcs_name_returns_none(_app, _registry):
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, eval_async=False)
    assert sched._derived("something_else") is None


# ---------------------------------------------------------------------------
# NEW: single bad column in snapshot — per-key arr() guard degrades gracefully
# ---------------------------------------------------------------------------
class _BadArr:
    """Simulates a Tiled array node whose .read() raises."""
    def read(self):
        raise RuntimeError("simulated tiled read error")


class _MixedSnap:
    """Snapshot where tau reads fine but g2_average raises."""
    def keys(self):
        return ["tau", "g2_average", "frames_count", "intensity_average"]

    def __getitem__(self, k):
        if k == "tau":
            return _Arr([1.0, 2.0])
        if k == "g2_average":
            return _BadArr()
        if k == "frames_count":
            return _Arr(99)
        if k == "intensity_average":
            return _Arr(5.5)
        raise KeyError(k)


def test_derived_bad_column_returns_none_for_that_field(_app, _registry, monkeypatch):
    """If one array's .read() raises, _derived('xpcs') must return a dict
    with that field set to None rather than raising or returning None for
    the whole result (per-key arr() guard)."""
    snap = _MixedSnap()
    xpcs_snap = type("_XpcsSnap", (), {
        "keys": lambda self: ["snapshot_001"],
        "__getitem__": lambda self, k: snap if k == "snapshot_001" else (_ for _ in ()).throw(KeyError(k)),
    })()
    run = type("_Run", (), {
        "__getitem__": lambda self, k: xpcs_snap if k == "xpcs" else (_ for _ in ()).throw(KeyError(k)),
    })()
    client = _Client({"u2": run})
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        staticmethod(lambda: _Svc(client)),
    )
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, eval_async=False)
    sched.start(); _arm(sched, eng, "u2")

    d = sched._derived("xpcs")

    # The whole dict must still come back (not None)
    assert d is not None, "_derived returned None instead of a partial dict"
    # tau read fine
    assert d["tau"] == [1.0, 2.0]
    # g2_average raised — must degrade to None, not propagate
    assert d["g2"]["average"] is None, (
        "Expected g2.average to be None when .read() raises, "
        f"got {d['g2']['average']!r}"
    )
    # frames_count converted correctly
    assert d["frames_count"] == 99
