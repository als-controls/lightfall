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
    # Avoid constructing a real scheduler/engine in the unit test.
    monkeypatch.setattr(MonitorService, "_build_scheduler", lambda self: None)
    svc = MonitorService.get_instance()
    yield svc
    MonitorService.reset_instance()


def test_recent_and_signal_on_observation(_svc):
    seen = []
    _svc.observation.connect(seen.append)
    obs = Observation(severity="info", feed_name="f", run_uid="u",
                      title="t", message="m", state_key="f:k")
    _svc._on_observation(obs)
    assert _svc.recent_observations()[-1] is obs
    assert seen == [obs]


def test_warn_triggers_toast(_svc, monkeypatch):
    calls = []
    monkeypatch.setattr(_svc, "_toast", lambda obs: calls.append(obs))
    warn = Observation(severity="warn", feed_name="f", run_uid="u",
                       title="t", message="m", state_key="f:k")
    _svc._on_observation(warn)
    assert calls == [warn]
