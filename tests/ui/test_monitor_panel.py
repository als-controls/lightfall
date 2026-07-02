import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.models import Observation
from lightfall.monitor.service import MonitorService


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def test_format_observation_includes_title_and_message():
    from lightfall.ui.panels.monitor_panel import format_observation
    obs = Observation(severity="warn", feed_name="health", run_uid="u",
                      title="Stalled", message="no events", state_key="k",
                      recommendation="check shutter")
    text = format_observation(obs)
    assert "Stalled" in text and "no events" in text and "check shutter" in text


def test_panel_adds_observation_row(_app, monkeypatch):
    monkeypatch.setattr(MonitorService, "_build_scheduler", lambda self: None)
    MonitorService.reset_instance()
    from lightfall.ui.panels.monitor_panel import MonitorPanel
    panel = MonitorPanel()
    before = panel.row_count()
    panel.add_observation(Observation(severity="info", feed_name="f", run_uid="u",
                                      title="t", message="m", state_key="k"))
    assert panel.row_count() == before + 1
    MonitorService.reset_instance()
