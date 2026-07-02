# tests/monitor/test_feed.py
from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.models import ExperimentContext, Observation


class _AlwaysWarn(MonitorFeed):
    name = "always_warn"
    default_interval_s = 15.0

    def evaluate(self, ctx, window, prior):
        return Observation(
            severity="warn", feed_name=self.name, run_uid=window.run_uid,
            title="t", message="m", state_key=f"{self.name}:x",
        )


def test_feed_subclass_evaluates():
    feed = _AlwaysWarn()
    obs = feed.evaluate(ExperimentContext.default(), DataWindow(run_uid="u1"), [])
    assert obs is not None and obs.feed_name == "always_warn"
    assert feed.default_interval_s == 15.0
