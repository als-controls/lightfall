import pytest
from PySide6.QtWidgets import QApplication
from lightfall.monitor.scheduler import MonitorScheduler


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _Eng:
    sigAbort = type("S", (), {"connect": lambda *a, **k: None})()
    sigException = type("S", (), {"connect": lambda *a, **k: None})()
    def subscribe(self, cb): return 1
    def unsubscribe(self, t): pass


def test_set_tick_interval_updates_timer(_app):
    sched = MonitorScheduler(_Eng(), eval_async=False, tick_granularity_s=5.0)
    sched.set_tick_interval_s(30.0)
    assert sched._timer.interval() == 30000
