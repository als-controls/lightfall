# tests/monitor/test_service_advisor.py
import pytest
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
