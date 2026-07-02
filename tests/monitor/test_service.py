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


def test_discuss_observation_submits_prompt_containing_title(_svc):
    """discuss_observation must activate the Claude panel and submit a prompt
    that contains the observation title."""

    class _StubPanel:
        def __init__(self):
            self.prompts: list[str] = []

        def submit_external_prompt(self, text: str) -> None:
            self.prompts.append(text)

    class _StubWindow:
        def __init__(self):
            self._panel = _StubPanel()

        def activate_panel(self, panel_id: str) -> None:
            pass  # no-op in test

        def get_panel(self, panel_id: str):
            return self._panel

    obs = Observation(severity="warn", feed_name="feed1", run_uid="abc123",
                      title="High noise", message="snr<2", state_key="feed1:noise")
    stub_win = _StubWindow()
    _svc.set_window(stub_win)
    _svc.discuss_observation(obs)

    assert stub_win._panel.prompts, "submit_external_prompt was never called"
    assert "High noise" in stub_win._panel.prompts[0], (
        "prompt must contain the observation title"
    )
    # Clean up — don't leave a window reference in the singleton.
    _svc.set_window(None)


def test_discuss_observation_no_raise_when_window_none(_svc):
    """discuss_observation must not raise when _window is None."""
    _svc.set_window(None)
    obs = Observation(severity="info", feed_name="f", run_uid="u",
                      title="t", message="m", state_key="f:k")
    _svc.discuss_observation(obs)  # must not raise
