# tests/monitor/test_service_advisor.py
import time
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from lightfall.monitor.models import Observation
from lightfall.monitor.service import MonitorService


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def _svc(_app, monkeypatch):
    MonitorService.reset_instance()
    monkeypatch.setattr(MonitorService, "_build_scheduler", lambda self: None)
    svc = MonitorService.get_instance()
    yield svc
    MonitorService.reset_instance()


class _FakeAdvisor:
    def __init__(self, reply): self.reply = reply; self.seen = None
    def advise(self, observations):
        self.seen = list(observations)
        return self.reply


def _obs(title):
    return Observation(severity="warn", feed_name="health", run_uid="u",
                       title=title, message="m", state_key=f"health:{title}")


def test_flush_runs_advisor_and_surfaces_reply_when_enabled(_svc, monkeypatch):
    monkeypatch.setattr(_svc, "_advisor_enabled", lambda: True)
    adv = _FakeAdvisor("FUSED MESSAGE")
    _svc.set_advisor(adv)
    # Force synchronous advisor execution for the test.
    _svc._advise_async = False

    surfaced = []
    _svc.observation.connect(surfaced.append)

    _svc._on_observation(_obs("A"))
    _svc._on_observation(_obs("B"))
    _svc._flush_advisor()  # the debounce timer would call this

    assert adv.seen is not None and len(adv.seen) == 2
    advisor_obs = [o for o in surfaced if o.feed_name == "advisor"]
    assert len(advisor_obs) == 1
    assert advisor_obs[0].message == "FUSED MESSAGE"
    assert advisor_obs[0].severity == "info"


def test_advisor_observations_are_not_rebatched(_svc, monkeypatch):
    monkeypatch.setattr(_svc, "_advisor_enabled", lambda: True)
    adv = _FakeAdvisor("X")
    _svc.set_advisor(adv); _svc._advise_async = False
    advisor_only = Observation(severity="info", feed_name="advisor", run_uid="u",
                               title="t", message="m", state_key="advisor:x")
    _svc._on_observation(advisor_only)
    _svc._flush_advisor()
    assert adv.seen in (None, [])  # advisor's own output never re-batched


def test_no_advisor_when_disabled(_svc, monkeypatch):
    monkeypatch.setattr(_svc, "_advisor_enabled", lambda: False)
    adv = _FakeAdvisor("X"); _svc.set_advisor(adv); _svc._advise_async = False
    _svc._on_observation(_obs("A"))
    _svc._flush_advisor()
    assert adv.seen is None


# ---------------------------------------------------------------------------
# NEW: async advisor path — exercises QThreadFuture -> _on_advisor_reply marshalling
# ---------------------------------------------------------------------------
def test_async_advisor_path_surfaces_reply(_svc, monkeypatch):
    """With _advise_async=True (the real default) the advisor result is
    marshalled back to the main thread via QThreadFuture's sigResult
    signal before being emitted as an observation."""
    monkeypatch.setattr(_svc, "_advisor_enabled", lambda: True)
    # Leave _advise_async at its default True value.
    adv = _FakeAdvisor("ASYNC REPLY")
    _svc.set_advisor(adv)
    assert _svc._advise_async is True  # confirm default

    surfaced = []
    _svc.observation.connect(surfaced.append)

    _svc._on_observation(_obs("X"))
    _svc._on_observation(_obs("Y"))
    _svc._flush_advisor()

    # Pump the event loop until an advisor observation arrives (up to 3 s).
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if any(o.feed_name == "advisor" for o in surfaced):
            break
        time.sleep(0.02)

    advisor_obs = [o for o in surfaced if o.feed_name == "advisor"]
    assert len(advisor_obs) == 1, (
        f"Expected 1 advisor observation but got {len(advisor_obs)}; "
        f"all surfaced={surfaced}"
    )
    assert advisor_obs[0].message == "ASYNC REPLY"
    assert advisor_obs[0].severity == "info"


# ---------------------------------------------------------------------------
# NEW: lazy advisor construction / never-built-when-disabled
# ---------------------------------------------------------------------------
def test_advisor_not_built_when_disabled(_svc, monkeypatch):
    """_ensure_advisor must NOT be reached (and MonitorAdvisor must never be
    instantiated) when the advisor is disabled, even after a flush with a
    non-empty batch."""
    monkeypatch.setattr(_svc, "_advisor_enabled", lambda: False)
    _svc._advisor = None  # start clean
    _svc._advise_async = False

    _svc._on_observation(_obs("A"))
    _svc._flush_advisor()

    # advisor should still be None — lazy build never triggered
    assert _svc._advisor is None


def test_advisor_lazy_build_on_first_flush(monkeypatch):
    """_ensure_advisor lazily instantiates MonitorAdvisor exactly once on
    the first enabled flush; subsequent flushes reuse the same instance.
    We patch MonitorAdvisor at its canonical location so no real SDK call fires."""
    MonitorService.reset_instance()
    monkeypatch.setattr(MonitorService, "_build_scheduler", lambda self: None)
    svc = MonitorService.get_instance()

    try:
        instances = []

        class _FakeMonitorAdvisor:
            def __init__(self):
                instances.append(self)

            def advise(self, observations):
                return "fake"

        # Patch at the canonical module so the lazy `from … import` picks it up.
        monkeypatch.setattr(
            "lightfall.monitor.advisor.MonitorAdvisor",
            _FakeMonitorAdvisor,
        )
        monkeypatch.setattr(svc, "_advisor_enabled", lambda: True)
        svc._advise_async = False

        assert svc._advisor is None  # not yet built

        # First flush with a non-empty batch should build the advisor.
        svc._advisor_batch = [_obs("P")]
        svc._flush_advisor()
        assert len(instances) == 1, "Expected exactly one instantiation"
        assert svc._advisor is instances[0]

        # Second flush reuses the same instance — no new construction.
        svc._advisor_batch = [_obs("Q")]
        svc._flush_advisor()
        assert len(instances) == 1, "Advisor must not be re-instantiated on second flush"
    finally:
        MonitorService.reset_instance()
